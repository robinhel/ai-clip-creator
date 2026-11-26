"""Samlingen av virala klipp: mätningar, rangordning och Apify-spärren. Inga riktiga anrop görs."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from campaign import apify, samling

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
TOKEN = "hemlig-testtoken-123"


@pytest.fixture
def con(tmp_path):
    return samling.connect(tmp_path / "samling.db")


def post(
    con, url: str, account: str, age_h: float, views: tuple[int, int], gap_h: float = 3.0
) -> None:
    """Ett inlägg skapat age_h timmar före NOW, mätt gap_h timmar före NOW och vid NOW."""
    created = NOW - timedelta(hours=age_h)
    for at, seen in ((NOW - timedelta(hours=gap_h), views[0]), (NOW, views[1])):
        samling.record(
            con,
            {
                "url": url,
                "platform": "tiktok",
                "account": account,
                "created": created,
                "title": "",
                "source": "apify",
                "views": seen,
            },
            at,
        )


def rows(lines: list[str], url: str) -> str:
    return next(line for line in lines if url in line)


def test_a_post_growing_five_times_faster_than_its_account_is_picked(con, tmp_path) -> None:
    # sneda värden: medianen 60 ger 5×, ett medelvärde (370) skulle inte välja star
    for n, per_h in enumerate((50, 60, 1000)):
        post(con, f"https://www.tiktok.com/@a/video/{n}", "a", 10 + n, (1000, 1000 + per_h * 3))
    post(con, "https://www.tiktok.com/@a/video/star", "a", 11, (1000, 1000 + 300 * 3))
    # mycket äldre inlägg från samma konto: får inte dra upp medianen
    post(
        con, "https://www.tiktok.com/@a/video/gammal-snabb", "a", 300, (10_000, 10_000 + 10_000 * 3)
    )
    # massor av visningar men långsam fart
    post(
        con,
        "https://www.tiktok.com/@a/video/gammal-stor",
        "a",
        320,
        (5_000_000, 5_000_000 + 20 * 3),
    )
    out = tmp_path / "samling" / "utvalda.txt"
    lines = samling.lista(con, out, NOW, 3.0)
    ranked = [line for line in lines if "tiktok.com" in line]
    assert "video/2" in ranked[0] and "video/star" in ranked[1]
    assert "5,0×" in rows(lines, "video/star")
    assert "kontot" in rows(lines, "video/star")
    assert "för få att jämföra med" in rows(lines, "gammal-stor")
    assert out.read_text(encoding="utf-8").split() == [
        "https://tiktok.com/@a/video/2",
        "https://tiktok.com/@a/video/star",
    ]


def test_an_account_with_one_post_is_compared_with_the_whole_collection(con, tmp_path) -> None:
    for n, per_h in enumerate((100, 200, 300)):
        post(con, f"https://www.tiktok.com/@k{n}/video/{n}", f"k{n}", 10, (0, per_h * 3))
    post(con, "https://www.tiktok.com/@solo/video/9", "solo", 10, (0, 600 * 3))
    lines = samling.lista(con, tmp_path / "utvalda.txt", NOW, 3.0)
    line = rows(lines, "@solo")
    assert "3,0×" in line and "hela samlingen" in line
    assert (tmp_path / "utvalda.txt").read_text(encoding="utf-8").split() == [
        "https://tiktok.com/@solo/video/9"
    ]


def test_measurements_less_than_two_hours_apart_give_no_speed(con, tmp_path) -> None:
    post(con, "https://www.tiktok.com/@a/video/1", "a", 10, (0, 9000), gap_h=1.5)
    text = "\n".join(samling.lista(con, tmp_path / "utvalda.txt", NOW, 3.0))
    assert "1 inlägg väntar" in text
    assert "mät igen om tidigast 2026-11-26 12:30 UTC" in text
    assert (tmp_path / "utvalda.txt").read_text(encoding="utf-8") == ""


def test_the_same_url_with_and_without_query_is_one_post(con) -> None:
    samling.add_links(
        con,
        [
            "https://www.tiktok.com/@a/video/1?lang=en&is_from=x",
            "http://www.tiktok.com/@a/video/1",
            "https://www.tiktok.com/@a/video/1/",
        ],
        NOW,
    )
    assert con.execute("SELECT url FROM posts").fetchall() == [("https://tiktok.com/@a/video/1",)]


def test_tiktok_with_and_without_www_is_one_post_and_two_youtube_ids_are_two(con) -> None:
    samling.add_links(
        con,
        [
            "https://www.tiktok.com/@a/video/1",
            "https://tiktok.com/@a/video/1",
            "https://www.youtube.com/watch?v=AAA&t=10s",
            "https://youtube.com/watch?v=BBB",
            "https://www.youtube.com/watch?v=AAA",
        ],
        NOW,
    )
    assert sorted(u for (u,) in con.execute("SELECT url FROM posts")) == [
        "https://tiktok.com/@a/video/1",
        "https://youtube.com/watch?v=AAA",
        "https://youtube.com/watch?v=BBB",
    ]


class FakeApify:
    """Låtsas-Apify. limits är månadens förbrukning, eller ett tal som är svarskoden när det är ett fel."""

    def __init__(
        self,
        usage: float | None,
        items: list[dict] | None = None,
        status: int = 200,
        run_error: int | Exception | None = None,
        start: str | None = None,
    ):
        self.usage, self.items, self.status = usage, items or [], status
        self.run_error, self.start = run_error, start
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert TOKEN not in str(request.url)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if request.url.path == "/v2/users/me/limits":
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"message": "nej"}})
            data = {"current": {"monthlyUsageUsd": self.usage}}
            if self.start:
                data["monthlyUsageCycle"] = {
                    "startAt": self.start,
                    "endAt": "2026-12-20T00:00:00.000Z",
                }
            return httpx.Response(200, json={"data": data})
        assert request.url.path == "/v2/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items"
        if isinstance(self.run_error, Exception):
            raise self.run_error
        if self.run_error:
            return httpx.Response(self.run_error, json={"error": {"message": "nej"}})
        return httpx.Response(201, json=self.items)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def runs(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


CONFIG = {
    "content_rewards": [],
    "apify": {"hashtags": ["bodycam"], "search": [], "profiles": [], "per_query": 5},
}
ITEMS = [
    {
        "webVideoUrl": "https://www.tiktok.com/@x/video/1?q=1",
        "playCount": 1000,
        "createTimeISO": "2026-11-26T02:00:00.000Z",
        "authorMeta": {"name": "x"},
        "text": "galet",
    },
    {
        "webVideoUrl": "https://www.tiktok.com/@y/video/2",
        "playCount": 50,
        "createTime": 1795600000,
        "authorMeta.name": "y",
        "text": "lugnt",
    },
]


def measure(con, fake: FakeApify, now=NOW, cfg=CONFIG, token=TOKEN, **kwargs) -> str:
    return "\n".join(samling.measure(con, cfg, token, now, http=fake.client(), **kwargs))


def test_apify_is_not_run_when_apify_says_the_month_is_almost_used(con) -> None:
    fake = FakeApify(4.46)
    text = measure(con, fake)
    assert not fake.runs() and "Apify körs inte" in text and TOKEN not in text


def test_with_ten_cents_left_a_run_may_cost_at_most_ten_cents(con) -> None:
    fake = FakeApify(4.40, ITEMS)
    measure(con, fake)
    assert float(fake.runs()[0].url.params["maxTotalChargeUsd"]) == 0.10


def test_apify_is_not_run_when_our_own_count_is_higher_than_apifys(con) -> None:
    con.execute(
        "INSERT INTO apify_runs (at, cost_usd) VALUES (?, ?)",
        ((NOW - timedelta(days=1)).isoformat(), 4.46),
    )
    fake = FakeApify(1.00)
    assert "Apify körs inte" in measure(con, fake)
    assert not fake.runs()


def test_apify_is_not_run_when_the_limits_cannot_be_read(con) -> None:
    fake = FakeApify(None, status=500)
    text = measure(con, fake)
    assert not fake.runs() and "Apify körs inte" in text and "förbrukning" in text


def test_a_run_is_capped_at_fifty_cents_and_its_cost_is_saved(con) -> None:
    fake = FakeApify(3.00, ITEMS)
    text = measure(con, fake)
    (run,) = fake.runs()
    assert float(run.url.params["maxTotalChargeUsd"]) == 0.5
    body = json.loads(run.content)
    assert body["hashtags"] == ["bodycam"] and body["resultsPerPage"] == 5
    assert all(body[key] is False for key in body if key.startswith("shouldDownload"))
    assert con.execute("SELECT SUM(cost_usd) FROM apify_runs").fetchone()[0] == pytest.approx(0.01)
    accounts = dict(con.execute("SELECT url, account FROM posts").fetchall())
    assert accounts == {"https://tiktok.com/@x/video/1": "x", "https://tiktok.com/@y/video/2": "y"}
    assert "Apify: 2 inlägg" in text


def test_the_second_apify_measurement_reuses_the_post_urls_instead_of_searching(con) -> None:
    measure(con, FakeApify(1.00, ITEMS))
    early = FakeApify(1.00, ITEMS)
    assert "mät igen om tidigast" in measure(con, early, now=NOW + timedelta(hours=1))
    assert not early.runs()
    later = FakeApify(1.00, ITEMS)
    measure(con, later, now=NOW + timedelta(hours=3))
    body = json.loads(later.runs()[0].content)
    assert sorted(body["postURLs"]) == [
        "https://tiktok.com/@x/video/1",
        "https://tiktok.com/@y/video/2",
    ]
    assert not body.get("hashtags")
    counts = con.execute("SELECT COUNT(*) FROM measurements GROUP BY url").fetchall()
    assert counts == [(2,), (2,)]


def test_without_a_token_apify_is_skipped_and_content_rewards_is_measured(con) -> None:
    clips = [
        {
            "post_url": "https://www.tiktok.com/@cr/video/7441234567890123456?x=1",
            "views": 9000,
            "platform": "tiktok",
            "title": "topp",
            "video_url": None,
            "name": "crnamn",
        }
    ]
    cfg = {**CONFIG, "content_rewards": ["https://contentrewards.com/c/campaigns/abc"]}
    fake = FakeApify(1.00)
    text = measure(con, fake, cfg=cfg, token="", cr_fetch=lambda url: {"clips": clips})
    assert "Apify hoppas över: APIFY_TOKEN saknas i .env." in text
    assert not fake.requests
    created, account = con.execute("SELECT created, account FROM posts").fetchone()
    assert account == "cr"
    assert created == datetime.fromtimestamp(7441234567890123456 >> 32, UTC).isoformat()
    assert con.execute("SELECT views FROM measurements").fetchall() == [(9000,)]


def test_a_failing_source_does_not_stop_the_others(con) -> None:
    samling.add_links(con, ["https://www.youtube.com/shorts/abc"], NOW)

    def cr_fetch(url):
        raise RuntimeError("Content Rewards svarar inte. Försök igen om en stund.")

    def info(url):
        return {"view_count": 42, "timestamp": 1795600000, "uploader": "robin", "title": "egen"}

    cfg = {**CONFIG, "content_rewards": ["https://contentrewards.com/c/campaigns/abc"]}
    text = measure(con, FakeApify(1.00, ITEMS), cfg=cfg, cr_fetch=cr_fetch, info=info)
    assert "Content Rewards svarar inte" in text
    assert con.execute("SELECT COUNT(*) FROM measurements").fetchone()[0] == 3


def test_a_missing_settings_file_points_to_the_template(tmp_path) -> None:
    with pytest.raises(SystemExit, match="samling.exempel.yaml"):
        samling.load_config(tmp_path)


def test_apify_items_are_read_flat_or_nested() -> None:
    posts = apify.parse(ITEMS)
    assert [p["account"] for p in posts] == ["x", "y"]
    assert posts[0]["created"] == datetime(2026, 11, 26, 2, tzinfo=UTC)
    assert posts[1]["created"] == datetime.fromtimestamp(1795600000, UTC)


def test_two_reservations_in_a_row_never_go_over_the_cap(con) -> None:
    _, first = samling.reserve(con, 4.00, None, NOW)
    assert first == 0.5
    with pytest.raises(apify.Stopped):  # Apify vet inte om den första än: reservationen räknas ändå
        samling.reserve(con, 4.00, None, NOW)
    assert 4.00 + con.execute("SELECT SUM(cost_usd) FROM apify_runs").fetchone()[0] <= 4.50


def test_the_second_reservation_only_gets_what_is_left(con) -> None:
    assert samling.reserve(con, 3.80, None, NOW)[1] == 0.5
    assert samling.reserve(con, 3.80, None, NOW)[1] == 0.2
    assert 3.80 + con.execute("SELECT SUM(cost_usd) FROM apify_runs").fetchone()[0] <= 4.50


@pytest.mark.parametrize("error", [500, httpx.ReadTimeout("väntade för länge")])
def test_an_error_or_timeout_from_the_run_leaves_the_whole_cap_saved(con, error) -> None:
    fake = FakeApify(3.00, ITEMS, run_error=error)
    text = measure(con, fake)
    assert len(fake.runs()) == 1 and "Apify körs inte" in text and "0.50" in text
    assert con.execute("SELECT cost_usd, done FROM apify_runs").fetchall() == [(0.5, 0)]
    assert not con.execute("SELECT COUNT(*) FROM posts").fetchone()[0]


def test_a_cost_before_apifys_cycle_start_is_not_counted(con) -> None:
    # 20 november: i kalendermånaden, men före Apifys period som börjar 21 november
    con.execute(
        "INSERT INTO apify_runs (at, cost_usd) VALUES (?, ?)",
        ((NOW - timedelta(days=6)).isoformat(), 4.46),
    )
    fake = FakeApify(1.00, ITEMS, start="2026-11-21T00:00:00.000Z")
    measure(con, fake)
    assert float(fake.runs()[0].url.params["maxTotalChargeUsd"]) == 0.5


def test_a_run_without_results_costs_at_least_one_cent(con) -> None:
    measure(con, FakeApify(1.00, []))
    assert con.execute("SELECT cost_usd, done FROM apify_runs").fetchall() == [(0.01, 1)]


def test_apify_that_is_not_a_mapping_gives_a_clear_message(tmp_path) -> None:
    (tmp_path / "samling.yaml").write_text("apify: bodycam\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="apify ha fälten"):
        samling.load_config(tmp_path)


def test_a_playlist_or_channel_link_gives_a_short_error_without_reading_it(
    con, monkeypatch
) -> None:
    import sys
    import types

    processed = []

    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True, process=True):
            if process:
                processed.append(url)
            return {"_type": "playlist", "entries": []}

    # en falsk modul, så att testet går också där yt-dlp (extrat viral) inte är installerat, som i CI
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    samling.add_links(con, ["https://www.youtube.com/@kanal"], NOW)
    text = measure(con, FakeApify(1.00, ITEMS))
    assert "spellista eller kanal" in text and not processed
