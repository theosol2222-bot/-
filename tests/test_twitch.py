import httpx

from clipfactory.twitch.chat import parse_privmsg
from clipfactory.twitch.helix import Helix
from clipfactory.twitch.recorder import Segment, SegmentTracker, map_wall_to_recording


def test_parse_privmsg():
    assert parse_privmsg(":nick!nick@nick.tmi.twitch.tv PRIVMSG #ZeratoR :mdrrr :)") == ("zerator", "mdrrr :)")
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan :Welcome") is None
    assert parse_privmsg(":a!a@a PRIVMSG #x :\x01ACTION danse\x01") == ("x", "danse")


def test_segment_tracker_real_streamlink8_format():
    t = SegmentTracker()
    t.feed("[stream.segmented][debug] Queuing TwitchHLSSegment(num=12, init=False, discontinuity=False, duration=2.002, "
           "title='live', key=None, byterange=None, date=2026-11-27T13:00:01.500000Z, map=None, ad=False, prefetch=False, fileext='ts')")
    seg = t.feed("[stream.hls][debug] Writing segment 12 to output")
    assert seg and seg.pos == 0 and abs(seg.date - 1795784401.5) < 1e-3 and abs(t.recorded - 2.002) < 1e-6


def test_chat_mapping_skips_ad_break():
    segs = [Segment(1, 0, 2, 1000.0), Segment(2, 2, 2, 1002.0), Segment(3, 4, 2, 1100.0)]  # pub de ~96 s retirée
    out = map_wall_to_recording([(1001.0, "a"), (1050.0, "pendant la pub"), (1101.0, "b"), (999.0, "avant")], segs)
    assert out == [(1.0, "a"), (5.0, "b")]


def test_helix_live_with_mock():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert req.headers["Client-Id"] == "cid" and req.headers["Authorization"] == "Bearer tok"
        assert req.url.params.get_list("user_login") == ["zerator", "gotaga"]
        return httpx.Response(200, json={"data": [{"user_login": "ZeratoR", "id": "42", "type": "live", "title": "ZEvent",
                                                   "game_name": "Just Chatting", "viewer_count": 51000,
                                                   "started_at": "2026-11-27T12:00:00Z"}]})

    h = Helix("cid", "sec", httpx.Client(transport=httpx.MockTransport(handler)))
    lives = h.live(["zerator", "gotaga"])
    assert list(lives) == ["zerator"] and lives["zerator"].viewers == 51000
    h.live(["zerator", "gotaga"])
    assert calls.count("/oauth2/token") == 1  # jeton réutilisé
