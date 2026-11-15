"""Kommandon: python -m campaign <kommando>. Kör `python -m campaign -h` för hjälp."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from .brain import Brain
from .budget import Budget, budget_from_settings
from .cliplog import POST_STATUSES, STATUSES, ClipLog
from .config import Settings, load_settings
from .facit import load_facit, score
from .media import probe
from .profile import Profile, load_profile
from .transcribe import EUR_PER_MINUTE, berget_post, transcribe_vod
from .workflow import finish_vod, load_picks


def _budget(settings: Settings) -> Budget:
    return budget_from_settings(settings)


def _log(settings: Settings) -> ClipLog:
    return ClipLog(settings.data_dir / "klipp.db")


def _profile(settings: Settings, name: str | None) -> Profile:
    name = name or os.environ.get("KLIPP_PROFILE")
    if not name:
        found = sorted(p.stem for p in settings.profiles_dir.glob("*.yaml") if not p.stem.startswith("_"))
        if len(found) != 1:
            raise SystemExit("Ange profil med --profile, eller sätt KLIPP_PROFILE i .env.")
        name = found[0]
    return load_profile(name, settings.profiles_dir)


def _print_budget(budget: Budget) -> None:
    s = budget.summary()
    print(f"Den här månaden: {s['month_usd']:.2f} av {s['month_cap_usd']:.2f} dollar")
    print(f"I dag:           {s['today_usd']:.2f} av {s['day_cap_usd']:.2f} dollar")
    for purpose, row in s["by_purpose"].items():
        print(f"  {purpose}: {row['calls']} anrop, {row['usd']:.4f} dollar")


def cmd_check_setup(args, settings: Settings) -> int:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        print(f"{'OK ' if found else 'FEL'} {tool}")
        ok = ok and bool(found)
    has_key = bool(settings.anthropic_api_key)
    print(f"{'OK ' if has_key else 'FEL'} ANTHROPIC_API_KEY i .env")
    ok = ok and has_key
    has_berget = bool(settings.berget_api_key)
    print(f"{'OK ' if has_berget else 'FEL'} BERGET_API_KEY i .env (transkribering)")
    ok = ok and has_berget
    profile = _profile(settings, args.profile)
    problems = profile.problems(settings.root)
    print(f"{'OK ' if not problems else 'FEL'} profilen {profile.name}")
    for problem in problems:
        print(f"    {problem}")
    ok = ok and not problems
    _print_budget(_budget(settings))
    print("Allt klart." if ok else "Rätta raderna med FEL ovan.")
    return 0 if ok else 1


def cmd_transcribe(args, settings: Settings) -> int:
    mp4 = settings.work_dir / args.vod_id / f"{args.vod_id}.mp4"
    out = mp4.parent / "transcript.json"
    next_step = (f"Kör sedan: clipsmith run-vod {args.vod_id} --config config.timmy.yaml "
                 "--skip-download --skip-transcribe")
    if not mp4.exists():
        raise SystemExit(f"Videon saknas: {mp4}")
    if out.exists() and not args.overwrite:
        print(f"{out} finns redan. Lägg till --overwrite för att göra om den.")
        print(next_step)
        return 0
    if not settings.berget_api_key:
        raise SystemExit("Fyll i BERGET_API_KEY i .env först.")
    model = args.model or settings.transcribe_model
    language = _profile(settings, args.profile).language
    minutes = probe(mp4)["duration"] / 60
    print(f"Transkriberar {minutes:.0f} minuter med {model} ({language}), "
          f"cirka {minutes * EUR_PER_MINUTE:.2f} euro.")
    transcript = transcribe_vod(mp4, args.vod_id, out, post=berget_post(settings.berget_api_key),
                                model=model, language=language)
    print(f"Klart: {len(transcript.segments)} meningar i {out}")
    print(next_step)
    return 0


def cmd_finish(args, settings: Settings) -> int:
    profile = _profile(settings, args.profile)
    for problem in profile.problems(settings.root):
        print(f"VARNING: {problem}")
    from .pipeline import signals_mode

    review = not args.no_review and not signals_mode(profile, settings)  # snabbläget: ingen Claude-budget
    brain = Brain(settings, _budget(settings)) if review else None
    results = finish_vod(
        args.vod_id, profile, settings, _log(settings), brain=brain,
        game=args.game, review=review, subdir=args.subdir,
    )
    for result in results:
        if "skipped" in result:
            print(f"HOPPAR ÖVER {result['file']}: {result['skipped']}")
            continue
        state = "UNDERKÄND" if result["problems"] else "KLAR"
        print(f"{state} #{result['id']} {result['file']}")
        for problem in result["problems"]:
            print(f"    problem: {problem}")
        for warning in result.get("warnings", []):
            print(f"    obs: {warning}")
    ready = sum(1 for r in results if "id" in r and not r["problems"])
    print(f"\n{ready} klipp väntar på ditt ja eller nej. Kör: python -m campaign approve")
    return 0


def cmd_local(args, settings: Settings) -> int:
    """En egen videofil som ett projekt i appen. Samma kedja som en körning från appen, utan Twitch."""
    from clipsmith.api.worker import _emit, _harvest_clips
    from clipsmith.db.models import Run, RunStatus
    from clipsmith.db.session import get_session, init_db
    from clipsmith.models.chat import ChatLog

    from .pipeline import clipsmith_config, run_campaign_vod

    src = Path(args.file)
    if not src.is_file() or src.suffix.lower() != ".mp4":
        raise SystemExit(f"Hittar ingen .mp4-fil: {src}")
    profile = _profile(settings, args.profile)
    vod_id = src.stem
    mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4"
    if args.only_register and not mp4.exists():
        raise SystemExit(f"{src.name} är inte körd: {mp4} saknas. Kör utan --only-register.")
    if not mp4.exists():
        print(f"Kopierar {src.name} till {mp4.parent}")
        mp4.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, mp4)
    chat = mp4.parent / "chat.json"
    if not chat.exists():  # en egen fil har ingen chatt; annars frågar clipsmith Twitch efter den
        chat.write_text(ChatLog(video_id=vod_id, messages=[]).to_json(), encoding="utf-8")

    init_db(settings.work_dir / "clipsmith.db")  # samma databas som servern
    db = get_session()
    try:
        run = Run(vod_id=vod_id, channel=profile.streamer, status=RunStatus.pending)
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    _log(settings).add_run(run_id, vod_id, profile.name, args.game, title=vod_id.replace("_", " ").strip(),
                           channel_name=profile.streamer, thumbnail_url=None,
                           duration_s=round(probe(mp4)["duration"]))
    if args.only_register:
        db = get_session()
        try:
            _harvest_clips(db, run_id, vod_id, clipsmith_config(profile, settings))
            _emit(db, run_id, "done", 100.0, "Registrerad. Klippen gjordes utanför appen.", status=RunStatus.done)
        finally:
            db.close()
    else:
        run_campaign_vod(run_id, vod_id, profile.name, args.game, settings, local=True)
    db = get_session()
    try:
        run = db.get(Run, run_id)
        status, error = run.status, run.error
    finally:
        db.close()
    print(f"Projekt {run_id}: {status.value}" + (f". {error}" if error else ""))
    return 0 if status == RunStatus.done else 1


def cmd_approve(args, settings: Settings) -> int:
    log = _log(settings)
    waiting = log.list("renderad")
    if not waiting:
        print("Inga klipp väntar.")
        return 0
    for clip in waiting:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        print(f"\n#{clip['id']}  {clip['title']}")
        print(f"  fil:     {clip['file']}")
        print(f"  caption: {clip['caption']}")
        for warning in clip["warnings"] or []:
            print(f"  obs:     {warning}")
        answer = input("  Godkänn? [j]a / [n]ej / [h]oppa över / [a]vsluta: ").strip().lower()
        if answer == "a":
            break
        if answer == "n":
            reason = input("  Varför inte? ").strip()
            log.update(clip["id"], status="nekad", note=reason)
        elif answer == "j":
            fields = {"status": "godkand"}
            if profile.formats and not clip["format"]:
                for number, fmt in enumerate(profile.formats, 1):
                    print(f"    {number}. {fmt['name']}")
                choice = input("  Vilket format? (nummer) ").strip()
                if choice.isdigit() and 0 < int(choice) <= len(profile.formats):
                    fields["format"] = profile.formats[int(choice) - 1]["id"]
            log.update(clip["id"], **fields)
    return cmd_status(args, settings)


def cmd_posted(args, settings: Settings) -> int:
    log = _log(settings)
    same = [p for p in log.posts(args.clip_id) if p["platform"] == (args.platform or "tiktok")]
    if same:  # samma plattform igen: byt länken
        log.update_post(same[0]["id"], url=args.url)
    else:  # ett inlägg per plattform
        log.add_post(args.clip_id, args.platform or "tiktok", args.url)
    print("Sparat. Skicka nu in länken i Content Rewards, och kör sedan:")
    print(f"  python -m campaign mark {args.clip_id} inskickad")
    return 0


def cmd_mark(args, settings: Settings) -> int:
    log = _log(settings)
    posts = log.posts(args.clip_id)
    if posts and args.status in POST_STATUSES:  # steget gäller alla klippets inlägg
        for post in posts:
            log.update_post(post["id"], status=args.status)
    else:
        log.update(args.clip_id, status=args.status)
    print(f"#{args.clip_id} har nu status {args.status}.")
    return 0


def cmd_stats(args, settings: Settings) -> int:
    log = _log(settings)
    fields = {k: getattr(args, k) for k in ("views", "likes", "comments", "shares")
              if getattr(args, k) is not None}
    posts = log.posts(args.clip_id)
    if len(posts) > 1:
        print("Klippet har flera inlägg. Fyll i siffrorna per inlägg under Statistik i appen.")
        return 1
    if posts:
        log.update_post(posts[0]["id"], **fields)
    else:
        log.update(args.clip_id, **fields)
    clip = log.get(args.clip_id)
    profile = load_profile(clip["profile"], settings.profiles_dir)
    rate = log.engagement(clip)
    print(f"#{clip['id']}: {clip['views']} visningar")
    if rate is not None:
        need = profile.engagement_min(clip["views"])
        verdict = "" if need is None else (" (klarar kravet)" if rate >= need else f" (kravet är {need} %)")
        print(f"  engagemang: {rate:.2f} %{verdict}")
    for item in log.todo(profile.min_views or 2000):
        if item.startswith(f"#{clip['id']} "):
            print(f"  ATT GÖRA: {item}")
    return 0


def cmd_status(args, settings: Settings) -> int:
    log = _log(settings)
    clips = log.list()
    print("\nKlipp per status:")
    for status in STATUSES:
        count = sum(1 for c in clips if c["status"] == status)
        if count:
            print(f"  {status}: {count}")
    todo = log.todo()
    if todo:
        print("\nAtt göra:")
        for item in todo:
            print(f"  {item}")
    print()
    _print_budget(_budget(settings))
    return 0


def cmd_budget(args, settings: Settings) -> int:
    _print_budget(_budget(settings))
    return 0


def cmd_facit(args, settings: Settings) -> int:
    facit = load_facit(Path(args.file))
    vod_id = args.vod or facit["vod"]
    if not vod_id:
        raise SystemExit("Ange VOD med --vod, eller skriv vod: i facit-filen.")
    picks = load_picks(settings.work_dir / vod_id / "picks.json")
    clips = [(p["start"], p["end"]) for p in picks if p["start"] is not None]
    result = score(facit["moments"], clips, args.tolerance)
    print(f"Verktyget hittade {result['hits']} av {result['total']} ögonblick i facit.")
    for moment in result["missed"]:
        minutes, seconds = divmod(int(moment["time"]), 60)
        print(f"  missade {minutes // 60}:{minutes % 60:02d}:{seconds:02d}  {moment['note']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m campaign", description="Kampanjlagret ovanpå clipsmith.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-setup", help="Kontrollera ffmpeg, nyckel, profil och budget")
    p.add_argument("--profile")
    p.set_defaults(func=cmd_check_setup)

    p = sub.add_parser("transcribe", help="Transkribera en nedladdad VOD via Berget")
    p.add_argument("vod_id", help="VOD-id, samma som mappnamnet i work/")
    p.add_argument("--model", help="Bergets modell, t.ex. Systran/faster-whisper-large-v3")
    p.add_argument("--profile")
    p.add_argument("--overwrite", action="store_true", help="Gör om en transkribering som finns")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("finish", help="Gör klippen från en VOD kampanjklara")
    p.add_argument("vod_id", help="VOD-id, samma som mappnamnet i out/")
    p.add_argument("--profile")
    p.add_argument("--game", help="Spelets namn, blir en hashtag")
    p.add_argument("--no-review", action="store_true", help="Hoppa över bildgranskningen")
    p.add_argument("--subdir", help="Undermapp i out/<vod>/, t.ex. stacked")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("local", help="Gör en egen videofil till ett projekt i appen och kör kedjan")
    p.add_argument("file", help="Sökväg till en .mp4-fil, till exempel en trailer från kampanjens mapp")
    p.add_argument("--profile")
    p.add_argument("--game", help="Spelets namn, blir en hashtag")
    p.add_argument("--only-register", action="store_true",
                   help="Filen är redan körd: gör bara projektet, kör inget om och anropa inte Claude")
    p.set_defaults(func=cmd_local)

    p = sub.add_parser("approve", help="Säg ja eller nej till klippen som väntar")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("posted", help="Markera ett klipp som postat")
    p.add_argument("clip_id", type=int)
    p.add_argument("url")
    p.add_argument("--platform", default="tiktok")
    p.set_defaults(func=cmd_posted)

    p = sub.add_parser("mark", help="Sätt status på ett klipp")
    p.add_argument("clip_id", type=int)
    p.add_argument("status", choices=STATUSES)
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("stats", help="Spara visningar och engagemang för ett klipp")
    p.add_argument("clip_id", type=int)
    p.add_argument("--views", type=int, required=True)
    p.add_argument("--likes", type=int)
    p.add_argument("--comments", type=int)
    p.add_argument("--shares", type=int)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("status", help="Klipplogg, att göra-lista och budget")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("budget", help="Visa vad AI-anropen har kostat")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("facit", help="Mät träffar mot dina handplockade ögonblick")
    p.add_argument("file", help="Facit-fil, t.ex. facit/min-vod.yaml")
    p.add_argument("--vod")
    p.add_argument("--tolerance", type=float, default=10.0)
    p.set_defaults(func=cmd_facit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, load_settings())
