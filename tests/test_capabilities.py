from __future__ import annotations

import json
from pathlib import Path

from flask import Flask

from aarkib.models.capabilities import (
    AudioCapabilities,
    ClientCapabilities,
    DeviceCapabilities,
    StreamingCapabilities,
    SubtitleCapabilities,
    VideoCapabilities,
)
from aarkib.services.capability_service import (
    capability_service,
    detect_client_capabilities,
)
from aarkib.services.transcoder import PlaybackStrategy, evaluate_playback_strategy


def test_capabilities_query_helpers() -> None:
    caps = ClientCapabilities(
        video=VideoCapabilities(
            codecs={"h264", "vp9", "av1"},
            containers={".mp4", ".webm"},
            max_width=1920,
            max_height=1080,
            supports_10bit=False,
            supports_hdr=False,
        ),
        audio=AudioCapabilities(
            codecs={"aac", "opus", "flac"},
            containers={".mp4", ".opus", ".flac"},
            max_channels=2,
        ),
        subtitles=SubtitleCapabilities(
            formats={"vtt"},
            supports_ass_wasm=True,
        ),
    )

    # Video codec and container checks (case insensitive, with/without dot)
    assert caps.supports_video("h264", ".mp4") is True
    assert caps.supports_video("H264", "mp4") is True
    assert caps.supports_video("vp9", ".webm") is True
    assert caps.supports_video("hevc", ".mp4") is False
    assert caps.supports_video("h264", ".mkv") is False

    # 10-bit check
    assert caps.supports_video("vp9", ".webm", is_10bit=True) is False

    # Dimension checks
    assert caps.supports_video("h264", ".mp4", width=1920, height=1080) is True
    assert caps.supports_video("h264", ".mp4", width=3840, height=2160) is False

    # Audio checks
    assert caps.supports_audio("aac", ".mp4") is True
    assert caps.supports_audio("AAC", "mp4") is True
    assert caps.supports_audio("dts", ".mp4") is False
    assert caps.supports_audio("opus", ".opus", channels=2) is True
    assert caps.supports_audio("opus", ".opus", channels=6) is False

    # Subtitles checks (native or WASM-rendered)
    assert caps.supports_subtitles("vtt") is True
    assert caps.supports_subtitles(".VTT") is True
    assert caps.supports_subtitles("ass") is True  # Supported via supports_ass_wasm
    assert caps.supports_subtitles("ssa") is True
    assert caps.supports_subtitles("pgs") is False


def test_capabilities_serialization_roundtrip() -> None:
    original = ClientCapabilities(
        video=VideoCapabilities(
            codecs={"h264", "hevc"},
            containers={".mp4", ".mov"},
            max_width=3840,
            max_height=2160,
            supports_10bit=True,
            supports_hdr=True,
            max_bitrate=25000,
        ),
        audio=AudioCapabilities(
            codecs={"aac", "alac"},
            containers={".mp4", ".m4a"},
            max_channels=6,
        ),
        subtitles=SubtitleCapabilities(
            formats={"vtt", "ass"},
            supports_ass_wasm=True,
        ),
        streaming=StreamingCapabilities(
            supports_hls=True,
            supports_byte_range=True,
        ),
        device=DeviceCapabilities(
            client_type="browser",
            client_name="Safari",
            platform="macOS",
            is_mobile=False,
            is_eink=False,
        ),
    )

    data = original.to_dict()
    assert isinstance(data, dict)
    assert data["video"]["codecs"] == ["h264", "hevc"]
    assert data["device"]["client_name"] == "Safari"

    restored = ClientCapabilities.from_dict(data)
    assert restored.video.codecs == {"h264", "hevc"}
    assert restored.video.containers == {".mp4", ".mov"}
    assert restored.video.supports_10bit is True
    assert restored.device.client_name == "Safari"
    assert restored.device.platform == "macOS"
    assert restored.supports_video("hevc", ".mp4", is_10bit=True) is True


def test_detect_chromium_browser() -> None:
    chrome_ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    headers = {
        "User-Agent": chrome_ua,
        "Sec-CH-UA-Platform": '"Linux"',
    }
    caps = capability_service.detect(override_headers=headers)

    assert caps.device.client_type == "browser"
    assert caps.device.client_name == "Chrome"
    assert caps.device.platform == "Linux"
    assert caps.device.is_mobile is False
    assert caps.supports_video("h264", ".mp4") is True
    assert caps.supports_video("vp9", ".webm") is True
    assert caps.supports_video("av1", ".mp4") is True
    assert caps.supports_audio("opus") is True
    assert caps.supports_audio("aac") is True
    assert caps.subtitles.supports_ass_wasm is True


def test_detect_edge_browser() -> None:
    edge_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
    )
    caps = capability_service.detect(override_headers={"User-Agent": edge_ua})

    assert caps.device.client_type == "browser"
    assert caps.device.client_name == "Edge"
    assert caps.device.platform == "Windows"


def test_detect_firefox_browser() -> None:
    firefox_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
    caps = capability_service.detect(override_headers={"User-Agent": firefox_ua})

    assert caps.device.client_type == "browser"
    assert caps.device.client_name == "Firefox"
    assert caps.device.platform == "Windows"
    assert caps.supports_video("vp9") is True
    assert caps.supports_video("av1") is True
    assert caps.supports_audio("flac") is True


def test_detect_safari_browser() -> None:
    safari_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    )
    caps = capability_service.detect(override_headers={"User-Agent": safari_ua})

    assert caps.device.client_type == "browser"
    assert caps.device.client_name == "Safari"
    assert caps.device.platform == "macOS"
    assert caps.supports_video("hevc", ".mp4", is_10bit=True) is True
    assert caps.streaming.supports_hls is True


def test_detect_koreader() -> None:
    koreader_ua = "KOReader/v2024.04 (Linux armv7l; Kobo Clara HD)"
    caps = capability_service.detect(override_headers={"User-Agent": koreader_ua})

    assert caps.device.client_type == "koreader"
    assert caps.device.client_name == "KOReader"
    assert caps.device.is_eink is True
    assert len(caps.video.codecs) == 0


def test_detect_jellyfin_client() -> None:
    headers = {
        "User-Agent": "Findroid/0.13.0 (Android 14)",
        "X-Emby-Client": "Findroid",
    }
    caps = capability_service.detect(override_headers=headers)

    assert caps.device.client_type == "jellyfin"
    assert caps.device.client_name == "Findroid"
    # Jellyfin ecosystem natively plays MKV and AC3
    assert caps.supports_video("h264", ".mkv") is True
    assert caps.supports_video("hevc", ".mkv") is True
    assert caps.supports_audio("ac3") is True
    assert caps.supports_audio("eac3") is True


def test_detect_subsonic_client() -> None:
    headers = {"User-Agent": "Symfonium/8.1.0 (Android)"}
    caps = capability_service.detect(override_headers=headers)

    assert caps.device.client_type == "subsonic"
    assert caps.device.client_name == "Symfonium"
    assert caps.supports_audio("flac") is True
    assert caps.supports_audio("opus") is True


def test_detect_baseline_fallback() -> None:
    # None / empty / unknown User-Agent
    caps = capability_service.detect(override_headers={})

    assert caps.device.client_type == "unknown"
    assert caps.device.client_name == "Unknown"
    # Baseline supports H.264 and AAC in MP4 container
    assert caps.supports_video("h264", ".mp4") is True
    assert caps.supports_video("hevc", ".mp4") is False
    assert caps.supports_video("h264", ".mkv") is False
    assert caps.supports_audio("aac", ".mp4") is True
    assert caps.supports_audio("ac3") is False


def test_explicit_json_override() -> None:
    custom_profile = {
        "video": {
            "codecs": ["av1"],
            "containers": [".webm"],
            "max_width": 1280,
            "max_height": 720,
            "supports_10bit": False,
        },
        "audio": {
            "codecs": ["opus"],
            "containers": [".webm"],
            "max_channels": 2,
        },
        "device": {
            "client_type": "custom_iot",
            "client_name": "SmartScreen",
        },
    }

    caps = capability_service.detect(override_json=custom_profile)

    assert caps.device.client_type == "custom_iot"
    assert caps.device.client_name == "SmartScreen"
    assert caps.supports_video("av1", ".webm") is True
    assert caps.supports_video("h264", ".mp4") is False
    assert caps.supports_audio("opus", ".webm") is True
    assert caps.supports_audio("aac", ".mp4") is False


def test_header_override_x_aarkib_capabilities(app: Flask) -> None:
    override_data = {
        "video": {"codecs": ["h264", "hevc"], "containers": [".mp4"]},
        "device": {"client_name": "CustomHeadless"},
    }
    with app.test_request_context(
        headers={"X-Aarkib-Capabilities": json.dumps(override_data)}
    ):
        caps = detect_client_capabilities()
        assert caps.device.client_name == "CustomHeadless"
        assert caps.supports_video("hevc", ".mp4") is True


def test_malformed_header_override_graceful_fallback(app: Flask) -> None:
    with app.test_request_context(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "X-Aarkib-Capabilities": "{malformed json ...",
        }
    ):
        caps = detect_client_capabilities()
        # Gracefully falls back to Firefox User-Agent detection without crashing
        assert caps.device.client_name == "Firefox"
        assert caps.supports_video("vp9") is True


def test_transcoder_integration_with_client_capabilities(tmp_path: Path) -> None:
    # Create a synthetic dummy MP4 path
    dummy_mp4 = tmp_path / "sample.mp4"
    dummy_mp4.touch()

    mock_streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac"}],
        "duration": 120.0,
    }

    # 1. Standard Chrome client -> DIRECT_PLAY
    chrome_caps = capability_service.detect(
        override_headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        }
    )
    res_direct = evaluate_playback_strategy(
        dummy_mp4, streams_info=mock_streams, client_caps=chrome_caps
    )
    assert res_direct["strategy"] == PlaybackStrategy.DIRECT_PLAY

    # 2. Restrictive client that does NOT support H.264 -> FULL_TRANSCODE
    restrictive_caps = ClientCapabilities(
        video=VideoCapabilities(codecs={"vp9"}, containers={".webm"}),
        audio=AudioCapabilities(codecs={"opus"}, containers={".webm"}),
    )
    res_transcode = evaluate_playback_strategy(
        dummy_mp4, streams_info=mock_streams, client_caps=restrictive_caps
    )
    assert res_transcode["strategy"] == PlaybackStrategy.FULL_TRANSCODE
    assert any(
        "Video codec 'h264' is not natively supported" in r
        for r in res_transcode["reasons"]
    )
