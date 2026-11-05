import pytest

from lovi.urls import InvalidLink, normalize_url


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/dQw4w9WgXcQ?si=tracking",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=abc&index=2",
        "https://m.youtube.com/shorts/dQw4w9WgXcQ",
        "https://youtube.com/embed/dQw4w9WgXcQ",
        "Вот ссылка https://youtube.com/watch?v=dQw4w9WgXcQ.",
    ],
)
def test_youtube_canonical(url):
    assert normalize_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://instagram.com/reels/AbC_12/?igsh=tracking",
            "https://www.instagram.com/reel/AbC_12/",
        ),
        ("https://www.instagram.com/reel/AbC_12/", "https://www.instagram.com/reel/AbC_12/"),
        ("https://vm.tiktok.com/AbC123/", "https://vm.tiktok.com/AbC123/"),
        ("https://vt.tiktok.com/AbC123/", "https://vt.tiktok.com/AbC123/"),
        (
            "https://www.tiktok.com/@user/video/12345?tracking=a",
            "https://www.tiktok.com/@user/video/12345",
        ),
    ],
)
def test_other_platforms(url, expected):
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/a",
        "https://169.254.169.254/latest/meta-data",
        "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://youtube.com@evil.example/watch?v=dQw4w9WgXcQ",
        "https://evil.example@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:8080/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:invalid/watch?v=dQw4w9WgXcQ",
        "file:///etc/passwd",
        "not a URL",
        "https://youtube.com/playlist?list=abc",
        "https://youtube.com/@channel",
        "https://youtu.be/invalid",
        "https://instagram.com/accounts/login",
        "https://tiktok.com/@channel",
        "https://youtu.be/dQw4w9WgXcQ https://youtu.be/dQw4w9WgXcQ",
        "https://[::1]/",
        "https://youtube.com/watch?v=<script>",
    ],
)
def test_reject_unknown_or_unsafe(url):
    with pytest.raises(InvalidLink):
        normalize_url(url)
