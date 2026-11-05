import pytest

from lovi.extractor import MediaError, check_info, summarize, video_format
from lovi.models import MediaSession, Selection
from lovi.ui import delivery_caption, media_card, media_keyboard


def fmt(key, width, height, *, audio=False, codec="avc1", ext="mp4"):
    return {
        "format_id": key,
        "width": width,
        "height": height,
        "ext": ext,
        "vcodec": codec,
        "acodec": "aac" if audio else "none",
        "protocol": "https",
    }


def test_portrait_format_selection():
    formats = [fmt("low", 608, 1080, audio=True), fmt("high", 1080, 1920, audio=True)]
    result = list(video_format(1080)({"formats": formats}))
    assert result[0]["format_id"] == "high"
    assert list(video_format(720)({"formats": formats}))[0]["format_id"] == "low"
    assert not list(video_format(360)({"formats": formats}))


def test_adaptive_video_keeps_audio():
    video = fmt("v", 1280, 720)
    audio = {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "aac", "protocol": "https"}
    selected = list(video_format(720)({"formats": [video, audio]}))[0]
    assert selected["requested_formats"] == [video, audio]
    assert selected["ext"] == "mp4"


def test_h264_preferred_at_equal_resolution():
    a, b = fmt("h264", 1280, 720, audio=True), fmt("av1", 1280, 720, audio=True, codec="av01")
    assert list(video_format(720)({"formats": [a, b]}))[0]["format_id"] == "h264"


def test_dont_invent_quality_buttons():
    info = summarize(
        {
            "extractor_key": "TikTok",
            "duration": 12,
            "formats": [fmt("only", 1080, 1920, audio=True)],
        },
        "https://tiktok.com",
    )
    assert info.heights == [1080]


def test_no_audio_track_is_not_silently_dropped():
    video = fmt("v", 1280, 720)
    audio = {"format_id": "a", "ext": "webm", "vcodec": "none", "acodec": "opus"}
    assert not list(video_format(720)({"formats": [video, audio]}))


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"is_live": True}, "live"),
        ({"live_status": "is_upcoming"}, "live"),
        ({"duration": 1000}, "duration"),
        ({"duration": None}, "duration"),
        ({"_type": "playlist"}, "unavailable"),
        ({"extractor_key": "Generic"}, "unavailable"),
    ],
)
def test_reject_unbounded_and_unsupported(extra, code):
    with pytest.raises(MediaError) as caught:
        check_info({"extractor_key": "Youtube", "duration": 10, **extra}, 60)
    assert caught.value.code == code


@pytest.mark.parametrize("key", ["v-1", "v9999", "v720:bad", "mp3", "../cover", "video"])
def test_forged_selection(key):
    with pytest.raises(ValueError):
        Selection.from_key(key)


def test_buttons_and_html_escaping(info):
    info.title = '<script> & "title"'
    info.author = "<author>"
    keyboard = media_keyboard(info, "x" * 12)
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert all(len(value.encode()) <= 64 for value in callbacks)
    assert len(callbacks) == 5
    assert "<script>" not in media_card(info)
    assert "<script>" not in delivery_caption(info, Selection(kind="audio"))
    assert "&lt;author&gt;" in media_card(info)


def test_format_not_available(info):
    session = MediaSession(user_id=1, chat_id=1, info=info)
    assert not session.permits(Selection(kind="video", height=2160))


def test_reels_missing_codec_is_progressive():
    fmt = {
        "format_id": "reel",
        "url": "https://cdn.example/reel.mp4",
        "ext": "mp4",
        "width": 1080,
        "height": 1920,
        "protocol": "https",
    }
    info = summarize(
        {"extractor_key": "Instagram", "duration": 10, "formats": [fmt]},
        "https://www.instagram.com/reel/example/",
    )
    assert info.has_audio
    assert info.heights == [1080]
    assert list(video_format(1080)({"formats": [fmt]})) == [fmt]


def test_unknown_dimensions_offer_original_only():
    fmt = {"format_id": "reel", "ext": "mp4", "protocol": "https"}
    info = summarize(
        {"extractor_key": "Instagram", "duration": 10, "formats": [fmt]},
        "https://www.instagram.com/reel/example/",
    )
    assert info.heights == [0]
    assert list(video_format(0)({"formats": [fmt]})) == [fmt]
    assert media_keyboard(info, "sid").inline_keyboard[0][0].text == "🎬 Оригинал"
