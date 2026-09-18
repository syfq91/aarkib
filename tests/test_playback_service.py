from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aarkib.models.capabilities import (
    AudioCapabilities,
    ClientCapabilities,
    DeviceCapabilities,
    SubtitleCapabilities,
    VideoCapabilities,
)
from aarkib.models.media import MediaType
from aarkib.models.media_item import MediaItem
from aarkib.models.playback import PlaybackMode, PlaybackPlan
from aarkib.services.playback_service import playback_service


@pytest.fixture
def desktop_chrome_caps() -> ClientCapabilities:
    return ClientCapabilities(
        video=VideoCapabilities(
            codecs={"h264", "vp8", "vp9", "av1"},
            containers={".mp4", ".webm"},
            supports_10bit=False,
            max_width=3840,
            max_height=2160,
        ),
        audio=AudioCapabilities(
            codecs={"aac", "mp3", "opus", "flac"},
            containers={".mp4", ".webm", ".mp3", ".flac"},
        ),
        subtitles=SubtitleCapabilities(
            formats={"vtt"},
            supports_ass_wasm=True,
        ),
        device=DeviceCapabilities(
            client_type="browser",
            client_name="Chrome",
            platform="Linux",
            is_mobile=False,
            is_eink=False,
        ),
    )


@pytest.fixture
def eink_koreader_caps() -> ClientCapabilities:
    return ClientCapabilities(
        video=VideoCapabilities(codecs=set(), containers=set()),
        audio=AudioCapabilities(codecs={"mp3"}, containers={".mp3"}),
        subtitles=SubtitleCapabilities(),
        device=DeviceCapabilities(
            client_type="koreader",
            client_name="KOReader",
            platform="E-Ink",
            is_mobile=False,
            is_eink=True,
        ),
    )


def test_video_direct_play(tmp_path: Path, desktop_chrome_caps: ClientCapabilities):
    mp4_file = tmp_path / "movie.mp4"
    mp4_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 120.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=42,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert isinstance(plan, PlaybackPlan)
    assert plan.mode == PlaybackMode.DIRECT
    assert plan.container == "mp4"
    assert plan.video_codec == "h264"
    assert plan.audio_codec == "aac"
    assert plan.stream_url == "/api/media/42/file"
    assert plan.direct_url == "/api/media/42/file"
    assert plan.diagnostics["copy_video"] is True
    assert plan.diagnostics["copy_audio"] is True


def test_video_direct_remux(tmp_path: Path, desktop_chrome_caps: ClientCapabilities):
    mkv_file = tmp_path / "movie.mkv"
    mkv_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 150.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mkv_file,
        media_item_id=55,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.REMUX
    assert plan.container == "mp4"
    assert plan.video_codec == "h264"
    assert plan.audio_codec == "aac"
    assert plan.stream_url == "/api/media/55/stream/remux"
    assert plan.direct_url == "/api/media/55/file"
    assert plan.diagnostics["remux"] is True
    assert plan.diagnostics["copy_video"] is True
    assert plan.diagnostics["copy_audio"] is True


def test_video_audio_transcode_with_direct_video(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mkv_file = tmp_path / "movie.mkv"
    mkv_file.write_bytes(b"dummy")

    # Audio is DTS (not in Chrome audio codecs)
    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "dts", "channels": 6}],
        "subtitles": [],
        "duration": 200.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mkv_file,
        media_item_id=77,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.TRANSCODE
    assert plan.container == "mp4"
    assert plan.video_codec == "h264"
    assert plan.audio_codec == "aac"
    assert "audio_transcode=true" in plan.stream_url
    assert plan.diagnostics["copy_video"] is True
    assert plan.diagnostics["transcode_audio"] is True


def test_video_full_transcode_unsupported_codec(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mp4_file = tmp_path / "movie.mp4"
    mp4_file.write_bytes(b"dummy")

    # HEVC is not in Chrome fixture video codecs
    streams = {
        "video": {"codec": "hevc", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 300.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=88,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.TRANSCODE
    assert plan.container == "m3u8"
    assert plan.video_codec == "h264"
    assert plan.audio_codec == "aac"
    assert "/stream/hls/master.m3u8" in plan.stream_url
    assert plan.diagnostics["transcode_video"] is True
    assert plan.diagnostics["copy_video"] is False


def test_video_full_transcode_10bit_color(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mp4_file = tmp_path / "hdr_movie.mp4"
    mp4_file.write_bytes(b"dummy")

    # 10-bit H.264
    streams = {
        "video": {
            "codec": "h264",
            "pix_fmt": "yuv420p10le",
            "width": 1920,
            "height": 1080,
        },
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 300.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=91,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.TRANSCODE
    assert any("10-bit" in r for r in plan.reasons)


def test_video_resolution_downscale_requested(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mp4_file = tmp_path / "hd.mp4"
    mp4_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 180.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=102,
        capabilities=desktop_chrome_caps,
        requested_resolution="480p",
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.TRANSCODE
    assert plan.resolution == "480p"
    assert plan.target_bitrate == 1000
    assert "res=480p" in plan.stream_url
    assert plan.diagnostics["downscale"] is True


def test_eink_epub_optimization(tmp_path: Path, eink_koreader_caps: ClientCapabilities):
    epub_file = tmp_path / "book.epub"
    epub_file.write_bytes(b"dummy_epub")

    plan = playback_service.plan_for_file(
        file_path=epub_file,
        media_item_id=11,
        capabilities=eink_koreader_caps,
    )

    assert plan.mode == PlaybackMode.OPTIMIZE
    assert plan.container == "epub"
    assert plan.stream_url == "/api/media/11/optimized"
    assert plan.direct_url == "/api/media/11/file"
    assert plan.diagnostics["optimized"] is True


def test_standard_epub_direct(tmp_path: Path, desktop_chrome_caps: ClientCapabilities):
    epub_file = tmp_path / "book.epub"
    epub_file.write_bytes(b"dummy_epub")

    plan = playback_service.plan_for_file(
        file_path=epub_file,
        media_item_id=12,
        capabilities=desktop_chrome_caps,
    )

    assert plan.mode == PlaybackMode.DIRECT
    assert plan.container == "epub"
    assert plan.stream_url == "/api/media/12/file"


def test_audio_direct_play(tmp_path: Path, desktop_chrome_caps: ClientCapabilities):
    flac_file = tmp_path / "song.flac"
    flac_file.write_bytes(b"dummy_audio")

    streams = {
        "audio": [{"codec": "flac", "channels": 2}],
        "duration": 240.0,
    }

    plan = playback_service.plan_for_file(
        file_path=flac_file,
        media_item_id=201,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.DIRECT
    assert plan.container == "flac"
    assert plan.audio_codec == "flac"
    assert plan.stream_url == "/api/media/201/file"


def test_audio_transcode_unsupported_codec(tmp_path: Path):
    wma_file = tmp_path / "song.wma"
    wma_file.write_bytes(b"dummy_wma")

    # Client supports only MP3
    caps = ClientCapabilities(
        audio=AudioCapabilities(codecs={"mp3"}, containers={".mp3"})
    )

    streams = {
        "audio": [{"codec": "wmav2", "channels": 2}],
        "duration": 210.0,
    }

    plan = playback_service.plan_for_file(
        file_path=wma_file,
        media_item_id=202,
        capabilities=caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.TRANSCODE
    assert plan.container == "mp3"
    assert plan.audio_codec == "mp3"
    assert plan.stream_url == "/api/media/202/stream/audio"


def test_subtitle_delivery_negotiation(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mkv_file = tmp_path / "anime.mkv"
    mkv_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [
            {
                "index": 0,
                "codec": "ass",
                "language": "eng",
                "title": "English ASS",
                "is_default": True,
            },
            {
                "index": 1,
                "codec": "subrip",
                "language": "spa",
                "title": "Spanish SRT",
                "is_default": False,
            },
            {
                "index": 2,
                "codec": "webvtt",
                "language": "fra",
                "title": "French VTT",
                "is_default": False,
            },
            {
                "index": 3,
                "codec": "hdmv_pgs_subtitle",
                "language": "deu",
                "title": "German PGS",
                "is_default": False,
            },
        ],
        "duration": 1400.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mkv_file,
        media_item_id=300,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert len(plan.subtitles) == 4
    # ASS with wasm support -> wasm delivery with .ass URL
    assert plan.subtitles[0]["delivery"] == "wasm"
    assert plan.subtitles[0]["url"] == "/api/media/300/subtitles/0.ass"
    # SRT -> vtt delivery with .vtt URL
    assert plan.subtitles[1]["delivery"] == "vtt"
    assert plan.subtitles[1]["url"] == "/api/media/300/subtitles/1.vtt"
    # WebVTT -> native delivery with .vtt URL
    assert plan.subtitles[2]["delivery"] == "native"
    assert plan.subtitles[2]["url"] == "/api/media/300/subtitles/2.vtt"
    # PGS bitmap -> burn_in delivery with no standalone URL
    assert plan.subtitles[3]["delivery"] == "burn_in"
    assert plan.subtitles[3]["url"] == ""


def test_playback_service_plan_media_item_integration(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mp4_file = tmp_path / "feature.mp4"
    mp4_file.write_bytes(b"dummy")

    mock_item = MagicMock(spec=MediaItem)
    mock_item.id = 707
    mock_item.original_file_path = str(mp4_file)
    mock_item.media_type = MediaType.VIDEO.value
    mock_item.file_format = "mp4"

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 600.0,
    }

    plan = playback_service.plan(
        item=mock_item,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan.mode == PlaybackMode.DIRECT
    assert plan.stream_url == "/api/media/707/file"
    assert plan.direct_url == "/api/media/707/file"


def test_deterministic_invariant(
    tmp_path: Path, desktop_chrome_caps: ClientCapabilities
):
    mp4_file = tmp_path / "constant.mp4"
    mp4_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 500.0,
    }

    plan1 = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=1,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )
    plan2 = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=1,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    assert plan1 == plan2
    assert plan1.to_dict() == plan2.to_dict()


def test_playback_plan_to_dict(tmp_path: Path, desktop_chrome_caps: ClientCapabilities):
    mp4_file = tmp_path / "serialized.mp4"
    mp4_file.write_bytes(b"dummy")

    streams = {
        "video": {"codec": "h264", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
        "duration": 10.0,
    }

    plan = playback_service.plan_for_file(
        file_path=mp4_file,
        media_item_id=99,
        capabilities=desktop_chrome_caps,
        streams_info=streams,
    )

    d = plan.to_dict()
    assert d["mode"] == "direct"
    assert d["container"] == "mp4"
    assert d["video_codec"] == "h264"
    assert d["audio_codec"] == "aac"
    assert isinstance(d["reasons"], list)
    assert isinstance(d["subtitles"], list)
    assert isinstance(d["diagnostics"], dict)
