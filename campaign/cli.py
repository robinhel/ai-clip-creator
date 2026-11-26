"""Kommandon: python -m campaign <kommando>. Kör `python -m campaign -h` för hjälp."""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
from pathlib import Path

from clipsmith.models.transcript import Transcript

from .brain import Brain
from .budget import Budget, budget_from_settings
from .cliplog import METRIC_KEYS, POST_STATUSES, STATUSES, ClipLog, UploadRunning
from .config import BERGET_MISSING, Settings, load_settings
from .facit import load_facit, score
from .local_whisper import loading_message, transcribe_local
from .media import probe
from .pipeline import clipsmith_config
from .profile import Profile, load_profile
from .transcribe import EUR_PER_MINUTE, berget_post, transcribe_vod
from .workflow import finish_vod, load_picks
from .schedule import ScheduleError, _require_campaign  # efter workflow: schedule och editor importerar varandra


def _budget(settings: Settings) -> Budget:
    return budget_from_settings(settings)


def _log(settings: Settings) -> ClipLog:
    return ClipLog(settings.data_dir / "klipp.db")


def _profile(settings: Settings, name: str | None) -> Profile:
    name = name or os.environ.get("KLIPP_PROFILE")
    if not name:
        found = sorted(p.stem for p in settings.profiles_dir.glob("*.yaml") if not p.stem.startswith("_"))
        if len(found) != 1:
            raise SystemExit("Choose a profile with --profile, or set KLIPP_PROFILE in .env.")
        name = found[0]
    return load_profile(name, settings.profiles_dir)


def _campaign_problem(clip: dict) -> str | None:
    """Felet för ett klipp utan kampanj (_ingen), som aldrig blir godkänt eller postat. Samma spärr som schemat."""
    try:
        _require_campaign(clip)
    except ScheduleError as error:
        return f"#{clip['id']}: {error}"
    return None


def _with_campaign(clips: list[dict]) -> list[dict]:
    """Klippen som går att godkänna: inte de utan kampanj. De nekas i appen eller med mark."""
    return [clip for clip in clips if not _campaign_problem(clip)]


def _post_problem(clip: dict) -> str | None:
    """Varför klippet inte får markeras som postat, annars None."""
    if clip["status"] not in ("godkand", *POST_STATUSES):  # samma regel som webben: bara godkända klipp postas
        return f"#{clip['id']} is {clip['status']}. Approve the clip before it is marked as posted."
    return _campaign_problem(clip)


def _print_budget(budget: Budget) -> None:
    s = budget.summary()
    print(f"This month: {s['month_usd']:.2f} of {s['month_cap_usd']:.2f} dollars")
    print(f"Today:      {s['today_usd']:.2f} of {s['day_cap_usd']:.2f} dollars")
    for purpose, row in s["by_purpose"].items():
        print(f"  {purpose}: {row['calls']} calls, {row['usd']:.4f} dollars")


def cmd_check_setup(args, settings: Settings) -> int:
    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        print(f"{'OK ' if found else 'FAIL'} {tool}")
        ok = ok and bool(found)
    has_key = bool(settings.anthropic_api_key)
    print(f"{'OK ' if has_key else 'FAIL'} ANTHROPIC_API_KEY in .env")
    ok = ok and has_key
    if settings.engine == "local":  # den lokala modellen behöver ingen nyckel
        print(f"OK  transcription: local Whisper {settings.local_whisper_model}")
    else:
        has_berget = bool(settings.berget_api_key)
        print("OK  BERGET_API_KEY in .env (transcription with Berget)" if has_berget else f"FAIL {BERGET_MISSING}")
        ok = ok and has_berget
    profile = _profile(settings, args.profile)
    problems = profile.problems(settings.root)
    print(f"{'OK ' if not problems else 'FAIL'} profile {profile.name}")
    for problem in problems:
        print(f"    {problem}")
    ok = ok and not problems
    _print_budget(_budget(settings))
    print("All good." if ok else "Fix the FAIL lines above.")
    return 0 if ok else 1


def cmd_transcribe(args, settings: Settings) -> int:
    mp4 = settings.work_dir / args.vod_id / f"{args.vod_id}.mp4"
    out = mp4.parent / "transcript.json"
    next_step = (f"Then run: clipsmith run-vod {args.vod_id} --config config.timmy.yaml "
                 "--skip-download --skip-transcribe")
    if not mp4.exists():
        raise SystemExit(f"The video is missing: {mp4}")
    if out.exists() and not args.overwrite:
        print(f"{out} already exists. Add --overwrite to redo it.")
        print(next_step)
        return 0
    profile = _profile(settings, args.profile)
    if settings.engine == "local":
        transcript = _transcribe_locally(mp4, profile, settings)
    else:
        transcript = _transcribe_with_berget(mp4, args.model, profile.language, settings)
    print(f"Done: {len(transcript.segments)} sentences in {out}")
    print(next_step)
    return 0


def _transcribe_locally(mp4: Path, profile: Profile, settings: Settings) -> Transcript:
    """work/<id>/<id>.mp4 till transcript.json bredvid, med kampanjens clipsmith-config och den valda modellen.
    Gratis på den här datorn, så ingen kostnadsrad."""
    model = settings.local_whisper_model
    try:
        config = clipsmith_config(profile, settings).transcribe
    except ValueError as error:
        raise SystemExit(str(error))
    print(f"{loading_message(model)}, then transcribing locally ({profile.language}).")
    return transcribe_local(mp4, mp4.stem, mp4.with_name("transcript.json"), config, model=model,
                            language=profile.language,
                            on_part=lambda n, total: print(f"Part {n} of {total}"))


def _transcribe_with_berget(mp4: Path, model: str | None, language: str, settings: Settings) -> Transcript:
    """work/<id>/<id>.mp4 till transcript.json bredvid, via Berget (kostar, se raden)."""
    if not settings.berget_api_key:
        raise SystemExit(BERGET_MISSING)
    model = model or settings.transcribe_model
    minutes = probe(mp4)["duration"] / 60
    print(f"Transcribing {minutes:.0f} minutes with {model} ({language}), "
          f"about {minutes * EUR_PER_MINUTE:.2f} euros.")
    return transcribe_vod(mp4, mp4.stem, mp4.with_name("transcript.json"), post=berget_post(settings.berget_api_key),
                          model=model, language=language)


def cmd_finish(args, settings: Settings) -> int:
    profile = _profile(settings, args.profile)
    for problem in profile.problems(settings.root):
        print(f"WARNING: {problem}")
    from .pipeline import signals_mode

    review = not args.no_review and not signals_mode(profile, settings)  # snabbläget: ingen Claude-budget
    brain = Brain(settings, _budget(settings)) if review else None
    results = finish_vod(
        args.vod_id, profile, settings, _log(settings), brain=brain,
        game=args.game, review=review, subdir=args.subdir,
    )
    for result in results:
        if "skipped" in result:
            print(f"SKIPPING {result['file']}: {result['skipped']}")
            continue
        state = "FAILED CHECK" if result["problems"] else "DONE"
        print(f"{state} #{result['id']} {result['file']}")
        for problem in result["problems"]:
            print(f"    problem: {problem}")
        for warning in result.get("warnings", []):
            print(f"    note: {warning}")
    ready = sum(1 for r in results if "id" in r and not r["problems"])
    print(f"\n{ready} {'clip is' if ready == 1 else 'clips are'} waiting for your yes or no. Run: python -m campaign approve")
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
        raise SystemExit(f"No .mp4 file found: {src}")
    profile = _profile(settings, args.profile)
    vod_id = src.stem
    mp4 = settings.work_dir / vod_id / f"{vod_id}.mp4"
    if args.only_register and not mp4.exists():
        raise SystemExit(f"{src.name} has not been run: {mp4} is missing. Run without --only-register.")
    if not mp4.exists():
        print(f"Copying {src.name} to {mp4.parent}")
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
            _emit(db, run_id, "done", 100.0, "Registered. The clips were made outside the app.", status=RunStatus.done)
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
    print(f"Project {run_id}: {status.value}" + (f". {error}" if error else ""))
    return 0 if status == RunStatus.done else 1


def cmd_approve(args, settings: Settings) -> int:
    log = _log(settings)
    waiting = _with_campaign(log.list("renderad"))
    if not waiting:
        print("No clips are waiting.")
        return 0
    for clip in waiting:
        profile = load_profile(clip["profile"], settings.profiles_dir)
        print(f"\n#{clip['id']}  {clip['title']}")
        print(f"  file:    {clip['file']}")
        print(f"  caption: {clip['caption']}")
        for warning in clip["warnings"] or []:
            print(f"  note:    {warning}")
        answer = input("  Approve? [y]es / [n]o / [s]kip / [q]uit: ").strip().lower()
        if answer in ("q", "a"):
            break
        if answer == "n":
            reason = input("  Why not? ").strip()
            log.update(clip["id"], status="nekad", note=reason)
        elif answer in ("y", "j"):
            fields = {"status": "godkand"}
            if profile.formats and not clip["format"]:
                for number, fmt in enumerate(profile.formats, 1):
                    print(f"    {number}. {fmt['name']}")
                choice = input("  Which format? (number) ").strip()
                if choice.isdigit() and 0 < int(choice) <= len(profile.formats):
                    fields["format"] = profile.formats[int(choice) - 1]["id"]
            log.update(clip["id"], **fields)
    return cmd_status(args, settings)


def cmd_posted(args, settings: Settings) -> int:
    log = _log(settings)
    problem = _post_problem(log.get(args.clip_id))
    if problem:
        print(problem)
        return 1
    platform = args.platform or "tiktok"
    same = [p for p in log.posts(args.clip_id) if p["platform"] == platform]
    if load_profile(log.get(args.clip_id)["profile"], settings.profiles_dir).multi(platform):
        same = [p for p in same if p["account"] is None]  # kontonas inlägg står kvar; för hand ett utan konto
    if same:  # samma plattform igen: byt länken
        log.update_post(same[0]["id"], url=args.url)
    else:  # ett inlägg per plattform (eller per konto)
        log.add_post(args.clip_id, platform, args.url)
    print("Saved. Now submit the link in Content Rewards, and then run:")
    print(f"  python -m campaign mark {args.clip_id} inskickad")
    return 0


def cmd_reframe(args, settings: Settings) -> int:
    from . import editor

    log, failed = _log(settings), 0
    for clip_id in args.clip_ids:
        clip = log.get(clip_id)
        try:
            if clip is None:
                raise ValueError("The clip does not exist.")
            editor.reframe_clip(clip, _profile(settings, clip["profile"]), settings, log)
        except (ValueError, RuntimeError, UploadRunning) as error:
            failed += 1
            print(f"ERROR #{clip_id}: {error}")
            continue
        clip = log.get(clip_id)
        print(f"{'FAILED CHECK' if clip['problems'] else 'DONE'} #{clip_id} {clip['file']}")
        for problem in clip["problems"] or []:
            print(f"    problem: {problem}")
    return 1 if failed else 0


def cmd_mark(args, settings: Settings) -> int:
    log = _log(settings)
    problem = _campaign_problem(log.get(args.clip_id)) if args.status in ("godkand", *POST_STATUSES) else None
    if problem:
        print(problem)
        return 1
    posts = log.posts(args.clip_id)
    if posts and args.status in POST_STATUSES:  # steget gäller alla klippets inlägg
        for post in posts:
            log.update_post(post["id"], status=args.status)
    else:
        log.update(args.clip_id, status=args.status)
    print(f"#{args.clip_id} now has status {args.status}.")
    return 0


GROUPED = re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?")  # 1,000.5: komma mellan tusental


def _decimal(value: str) -> float:
    """Ett tal i engelskt format (6.4 eller 1,000.5), 0 eller mer."""
    try:
        number = float(value.replace(",", "") if GROUPED.fullmatch(value) else value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number.") from error
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(f"{value!r} is not a number.")
    if number < 0:
        raise argparse.ArgumentTypeError("The number cannot be less than 0.")
    return number


def _count(value: str) -> int:
    """Ett heltal, 0 eller mer."""
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number.") from error
    if number < 0:
        raise argparse.ArgumentTypeError("The number cannot be less than 0.")
    return number


def _percent(value: str) -> float:
    number = _decimal(value)
    if number > 100:
        raise argparse.ArgumentTypeError("A share is at most 100 percent.")
    return number


def _stats_blocked(posts: list[dict], fields: dict) -> str | None:
    """Varför siffrorna inte går att spara här, None när de går."""
    if len(posts) > 1:
        return "The clip has several posts. Fill in the numbers per post under Stats in the app."
    if not posts and set(METRIC_KEYS) & fields.keys():
        return "More metrics are saved per post. Save the link first with python -m campaign posted."
    return None


def cmd_stats(args, settings: Settings) -> int:
    log = _log(settings)
    fields = {k: getattr(args, k) for k in ("views", "likes", "comments", "shares", *METRIC_KEYS)
              if getattr(args, k) is not None}
    posts = log.posts(args.clip_id)
    blocked = _stats_blocked(posts, fields)
    if blocked:
        print(blocked)
        return 1
    if posts:
        log.update_post(posts[0]["id"], **fields)
    else:
        log.update(args.clip_id, **fields)
    clip = log.get(args.clip_id)
    profile = load_profile(clip["profile"], settings.profiles_dir)
    rate = log.engagement(clip)
    print(f"#{clip['id']}: {clip['views']} views")
    if rate is not None:
        need = profile.engagement_min(clip["views"])
        verdict = "" if need is None else (" (meets the requirement)" if rate >= need else f" (the requirement is {need} %)")
        print(f"  engagement: {rate:.2f} %{verdict}")
    for item in log.todo(profile.min_views or 2000):
        if item.startswith(f"#{clip['id']} "):
            print(f"  TO DO: {item}")
    return 0


def cmd_status(args, settings: Settings) -> int:
    log = _log(settings)
    clips = log.list()
    print("\nClips per status:")
    for status in STATUSES:
        count = sum(1 for c in clips if c["status"] == status)
        if count:
            print(f"  {status}: {count}")
    todo = log.todo()
    if todo:
        print("\nTo do:")
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
        raise SystemExit("Choose a VOD with --vod, or write vod: in the facit file.")
    picks = load_picks(settings.work_dir / vod_id / "picks.json")
    clips = [(p["start"], p["end"]) for p in picks if p["start"] is not None]
    result = score(facit["moments"], clips, args.tolerance)
    print(f"The tool found {result['hits']} of {result['total']} moments in the facit.")
    for moment in result["missed"]:
        minutes, seconds = divmod(int(moment["time"]), 60)
        print(f"  missed {minutes // 60}:{minutes % 60:02d}:{seconds:02d}  {moment['note']}")
    return 0


def _print_viral(clips: list[dict]) -> None:
    for clip in clips:
        place = clip.get("location")
        where = f"VOD {place['vod_id']} at {place['start_s']:.0f} s" if place else "not found in the stream"
        print(f"  {clip.get('outlier') or 0:.1f}x  {clip.get('views') or 0:,} views  {clip.get('title') or clip['url']}"
              f"  ({clip.get('source')}, {where})")


def _viral_collect(args, profile: Profile, settings: Settings) -> None:
    from . import viral

    clips = viral.collect(profile)
    print(f"{len(clips)} viral {'clip' if len(clips) == 1 else 'clips'} for {profile.name}:")
    _print_viral(clips)


def _viral_link(args, profile: Profile, settings: Settings) -> None:
    from . import viral

    _print_viral([viral.add_link(profile, args.url, views=args.views)])


def _viral_analyse(args, profile: Profile, settings: Settings) -> None:
    from . import taste

    brain = Brain(settings, _budget(settings))
    if not brain.available:
        raise ValueError("ANTHROPIC_API_KEY is missing in .env. Add it and analyse again.")
    found = taste.analyse(profile, top_n=args.top, brain=brain)
    for rule in found.get("rules", []):
        print(f"  - {rule}")
    print(found.get("summary_en", ""))


def _viral_skills(args, profile: Profile, settings: Settings) -> None:
    from . import taste

    for path in taste.write_skills(profile):
        print(f"Wrote {path}")


VIRAL = {"collect": _viral_collect, "link": _viral_link, "analyse": _viral_analyse, "skills": _viral_skills}


def cmd_viral(args, settings: Settings) -> int:
    """Smaken från virala klipp: hämta, lägg till en länk, analysera och skriv skills (campaign/viral.py, taste.py)."""
    profile = _profile(settings, args.profile)
    try:
        VIRAL[args.viral_command](args, profile, settings)
    except (ValueError, RuntimeError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


def _add_viral(sub) -> None:
    p = sub.add_parser("viral", help="Learn the taste from other people's viral clips")
    viral = p.add_subparsers(dest="viral_command", required=True)
    v = viral.add_parser("collect", help="Fetch the campaign's viral clips and find the outliers")
    v.add_argument("profile")
    v = viral.add_parser("link", help="Add a viral clip from a link")
    v.add_argument("profile")
    v.add_argument("url")
    v.add_argument("--views", type=_count, help="The clip's views, if the page does not show them")
    v = viral.add_parser("analyse", help="Ask Claude how the top outliers were made and save it as the taste")
    v.add_argument("profile")
    v.add_argument("--top", type=_count, default=5, help="How many outliers to analyse (default 5)")
    v = viral.add_parser("skills", help="Write the taste as skills files")
    v.add_argument("profile")
    p.set_defaults(func=cmd_viral)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m campaign", description="The campaign layer on top of clipsmith.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check-setup", help="Check ffmpeg, key, profile and budget")
    p.add_argument("--profile")
    p.set_defaults(func=cmd_check_setup)

    p = sub.add_parser("transcribe", help="Transcribe a downloaded VOD (Berget or local Whisper, as in Settings)")
    p.add_argument("vod_id", help="VOD ID, same as the folder name in work/")
    p.add_argument("--model", help="Berget's model, e.g. Systran/faster-whisper-large-v3 (Berget only)")
    p.add_argument("--profile")
    p.add_argument("--overwrite", action="store_true", help="Redo an existing transcription")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("finish", help="Make the clips from a VOD campaign-ready")
    p.add_argument("vod_id", help="VOD ID, same as the folder name in out/")
    p.add_argument("--profile")
    p.add_argument("--game", help="The game's name, becomes a hashtag")
    p.add_argument("--no-review", action="store_true", help="Skip the image review")
    p.add_argument("--subdir", help="Subfolder in out/<vod>/, e.g. stacked")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("local", help="Turn your own video file into a project in the app and run the chain")
    p.add_argument("file", help="Path to an .mp4 file, for example a trailer from the campaign's folder")
    p.add_argument("--profile")
    p.add_argument("--game", help="The game's name, becomes a hashtag")
    p.add_argument("--only-register", action="store_true",
                   help="The file has already been run: only make the project, rerun nothing and do not call Claude")
    p.set_defaults(func=cmd_local)

    p = sub.add_parser("approve", help="Say yes or no to the waiting clips")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("posted", help="Mark a clip as posted")
    p.add_argument("clip_id", type=int)
    p.add_argument("url")
    p.add_argument("--platform", default="tiktok")
    p.set_defaults(func=cmd_posted)

    p = sub.add_parser("reframe", help="Remake clips in the campaign's layout; headline and words are kept")
    p.add_argument("clip_ids", type=int, nargs="+")
    p.set_defaults(func=cmd_reframe)

    p = sub.add_parser("mark", help="Set the status of a clip")
    p.add_argument("clip_id", type=int)
    p.add_argument("status", choices=STATUSES)
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("stats", help="Save views and engagement for a clip")
    p.add_argument("clip_id", type=int)
    p.add_argument("--views", type=int, required=True)
    p.add_argument("--likes", type=int)
    p.add_argument("--comments", type=int)
    p.add_argument("--shares", type=int)
    p.add_argument("--watch-s", dest="avg_watch_s", type=_decimal, help="average watch time in seconds")
    p.add_argument("--full-pct", dest="full_pct", type=_percent, help="share who watch to the end, in percent")
    p.add_argument("--fyp-pct", dest="fyp_pct", type=_percent, help="share of views from For You, in percent")
    p.add_argument("--countries-pct", dest="countries_pct", type=_percent,
                   help="share of views from the campaign's countries, in percent")
    p.add_argument("--saves", type=_count, help="number of saves")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("status", help="Clip log, to-do list and budget")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("budget", help="Show what the AI calls have cost")
    p.set_defaults(func=cmd_budget)

    p = sub.add_parser("facit", help="Measure hits against your hand-picked moments")
    p.add_argument("file", help="Facit file, e.g. facit/min-vod.yaml")
    p.add_argument("--vod")
    p.add_argument("--tolerance", type=float, default=10.0)
    p.set_defaults(func=cmd_facit)

    _add_viral(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, load_settings())
