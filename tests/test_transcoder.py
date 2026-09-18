from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from aarkib.models import MediaItem
from aarkib.services.transcoder import (
    PlaybackStrategy,
    TranscodeSupervisor,
    detect_vaapi_device,
    evaluate_playback_strategy,
    generate_ass_subtitles,
    generate_vtt_subtitles,
    probe_media_streams,
    reset_vaapi_cache,
    transcode_supervisor,
)
from tests.test_video import create_synthetic_mp4


def test_probe_media_streams_synthetic(tmp_path):
    mp4_file = tmp_path / "sample.mp4"
    create_synthetic_mp4(mp4_file, duration_sec=90, width=1920, height=1080)

    res = probe_media_streams(mp4_file)
    assert res["container"] == ".mp4"
    assert res["duration"] == 90.0
    assert res["video"] is not None
    assert res["video"]["width"] == 1920
    assert res["video"]["height"] == 1080


def test_evaluate_playback_strategy_direct_play(tmp_path):
    mp4_file = tmp_path / "movie.mp4"
    streams = {
        "container": ".mp4",
        "video": {"codec": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
    }
    decision = evaluate_playback_strategy(mp4_file, streams)
    assert decision["strategy"] == PlaybackStrategy.DIRECT_PLAY.value
    assert decision["container_native"] is True
    assert decision["video_native"] is True
    assert decision["audio_native"] is True


def test_evaluate_playback_strategy_direct_remux(tmp_path):
    mkv_file = tmp_path / "show.mkv"
    streams = {
        "container": ".mkv",
        "video": {"codec": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
    }
    decision = evaluate_playback_strategy(mkv_file, streams)
    assert decision["strategy"] == PlaybackStrategy.DIRECT_REMUX.value
    assert decision["container_native"] is False
    assert decision["video_native"] is True
    assert any("remux" in r.lower() for r in decision["reasons"])


def test_evaluate_playback_strategy_audio_transcode(tmp_path):
    mkv_file = tmp_path / "movie.mkv"
    streams = {
        "container": ".mkv",
        "video": {"codec": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
        "audio": [{"codec": "dts", "channels": 6}],
        "subtitles": [],
    }
    decision = evaluate_playback_strategy(mkv_file, streams)
    assert decision["strategy"] == PlaybackStrategy.AUDIO_TRANSCODE.value
    assert decision["audio_native"] is False
    assert any("audio" in r.lower() for r in decision["reasons"])


def test_evaluate_playback_strategy_full_transcode_hevc(tmp_path):
    mp4_file = tmp_path / "4k_hevc.mp4"
    streams = {
        "container": ".mp4",
        "video": {
            "codec": "hevc",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
        },
        "audio": [{"codec": "aac", "channels": 2}],
        "subtitles": [],
    }
    decision = evaluate_playback_strategy(mp4_file, streams)
    assert decision["strategy"] == PlaybackStrategy.FULL_TRANSCODE.value
    assert decision["video_native"] is False


def test_detect_vaapi_device_override():
    res = detect_vaapi_device("/dev/dri/custom_device")
    assert res == "/dev/dri/custom_device"


def test_detect_vaapi_device_fallback_when_absent():
    reset_vaapi_cache()
    with (
        patch("os.getenv", return_value=None),
        patch("pathlib.Path.is_dir", return_value=False),
    ):
        dev = detect_vaapi_device()
        assert dev is None
    reset_vaapi_cache()


def test_transcode_supervisor_lifecycle(tmp_path):
    supervisor = TranscodeSupervisor(idle_timeout=1.0)
    base_dir = tmp_path / "transcode_base"
    base_dir.mkdir(parents=True, exist_ok=True)

    dummy_file = tmp_path / "dummy.mp4"
    dummy_file.write_bytes(b"0" * 100)

    # Mock subprocess.Popen to prevent actually spawning ffmpeg in this lifecycle test
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc):
        session = supervisor.create_or_get_hls_session(
            media_item_id=42,
            file_path=dummy_file,
            transcode_base_dir=base_dir,
            resolution="720p",
        )

        assert session is not None
        assert session.media_item_id == 42
        assert session.target_resolution == "720p"
        assert session.output_dir.exists()

        # Touch session
        old_activity = session.last_activity
        time.sleep(0.05)
        supervisor.touch_session(session.session_id)
        assert session.last_activity > old_activity

        # Stop session
        supervisor.stop_session(session.session_id)
        assert supervisor.get_session(session.session_id) is None
        assert not session.output_dir.exists()

    supervisor.cleanup_all()


def test_transcode_supervisor_reaper_cleans_expired(tmp_path):
    supervisor = TranscodeSupervisor(idle_timeout=10.0, reaper_interval=0.05)
    base_dir = tmp_path / "transcode_reap"
    base_dir.mkdir(parents=True, exist_ok=True)

    dummy_file = tmp_path / "dummy_reap.mp4"
    dummy_file.write_bytes(b"0" * 100)

    mock_proc = MagicMock()
    mock_proc.pid = 54321
    mock_proc.poll.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc):
        session = supervisor.create_or_get_hls_session(
            media_item_id=99,
            file_path=dummy_file,
            transcode_base_dir=base_dir,
        )
        sid = session.session_id
        session_dir = session.output_dir
        assert session_dir.exists()

        # Age the session beyond idle_timeout to trigger background reaper
        session.last_activity = time.time() - 20.0

        # Wait for the background reaper thread to automatically clean it up
        reaped = False
        for _ in range(40):
            if supervisor.get_session(sid) is None and not session_dir.exists():
                reaped = True
                break
            time.sleep(0.05)

        assert reaped is True
        assert supervisor.get_session(sid) is None
        assert not session_dir.exists()

    supervisor.cleanup_all()


def test_clean_stale_directories(tmp_path):
    supervisor = TranscodeSupervisor()
    base_dir = tmp_path / "stale_transcode"
    base_dir.mkdir()

    stale1 = base_dir / "hls_10_abc"
    stale1.mkdir()
    stale2 = base_dir / "hls_11_def"
    stale2.mkdir()
    other = base_dir / "other_folder"
    other.mkdir()

    supervisor.clean_stale_directories(base_dir)
    assert not stale1.exists()
    assert not stale2.exists()
    assert other.exists()
    supervisor.cleanup_all()


def test_generate_vtt_subtitles_fallback(tmp_path):
    missing_file = tmp_path / "missing.mkv"
    vtt = generate_vtt_subtitles(missing_file, 0)
    assert vtt.startswith(b"WEBVTT")


def test_generate_ass_subtitles_fallback(tmp_path):
    missing_file = tmp_path / "missing.mkv"
    ass = generate_ass_subtitles(missing_file, 0)
    assert ass.startswith(b"[Script Info]")


def test_stream_api_endpoints(client, app, tmp_path):
    video_file = tmp_path / "stream_sample.mp4"
    create_synthetic_mp4(video_file, duration_sec=60, width=1280, height=720)

    with app.app_context():
        from aarkib.extensions import db

        item = MediaItem(
            title="Stream Test Video",
            original_file_path=str(video_file),
            file_format="mp4",
            file_size=video_file.stat().st_size,
            file_hash="dummy_hash_123",
            media_type="video",
            duration=60.0,
            resolution_width=1280,
            resolution_height=720,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    # 1. GET /api/stream/<id>/info
    res = client.get(f"/api/stream/{item_id}/info")
    assert res.status_code == 200
    data = res.get_json()
    assert data["id"] == item_id
    assert data["file_format"] == "mp4"
    assert "streams" in data
    assert "evaluation" in data
    assert "hls_url" in data

    # 2. GET /api/stream/<id>/subtitles
    res_subs = client.get(f"/api/stream/{item_id}/subtitles")
    assert res_subs.status_code == 200
    assert "subtitles" in res_subs.get_json()

    # 3. GET /api/stream/<id>/subtitles/0.vtt
    res_vtt = client.get(f"/api/stream/{item_id}/subtitles/0.vtt")
    assert res_vtt.status_code == 200
    assert "text/vtt" in res_vtt.content_type
    assert res_vtt.data.startswith(b"WEBVTT")

    # 3b. GET /api/stream/<id>/subtitles/0.ass
    res_ass = client.get(f"/api/stream/{item_id}/subtitles/0.ass")
    assert res_ass.status_code == 200
    assert "text/x-ssa" in res_ass.content_type
    assert res_ass.data.startswith(b"[Script Info]")

    # 4. GET /api/stream/<id>/hls/master.m3u8
    transcode_dir = tmp_path / "transcode_test"
    transcode_dir.mkdir(parents=True, exist_ok=True)
    app.config["TRANSCODE_DIR"] = transcode_dir

    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.poll.return_value = None

    with patch("subprocess.Popen", return_value=mock_proc):
        res_hls = client.get(f"/api/stream/{item_id}/hls/master.m3u8?resolution=720p")
        assert res_hls.status_code == 200
        assert "application/vnd.apple.mpegurl" in res_hls.content_type
        assert b"#EXTM3U" in res_hls.data
        assert b"playlist.m3u8" in res_hls.data

        # Extract session_id from master playlist
        playlist_line = [
            line
            for line in res_hls.data.decode().splitlines()
            if "playlist.m3u8" in line
        ][0]
        session_id = playlist_line.split("/hls/")[1].split("/playlist.m3u8")[0]

        session = transcode_supervisor.get_session(session_id)
        assert session is not None

        # Create dummy playlist and segment files inside session directory
        (session.output_dir / "playlist.m3u8").write_text(
            "#EXTM3U\n#EXT-X-TARGETDURATION:6\n"
        )
        (session.output_dir / "segment_00000.m4s").write_bytes(b"segment bytes")

        # 5. GET /api/stream/<id>/hls/<session_id>/playlist.m3u8
        res_sess_m3u8 = client.get(
            f"/api/stream/{item_id}/hls/{session_id}/playlist.m3u8"
        )
        assert res_sess_m3u8.status_code == 200
        assert b"#EXT-X-TARGETDURATION:6" in res_sess_m3u8.data

        # 6. GET /api/stream/<id>/hls/<session_id>/segment_00000.m4s
        res_seg = client.get(
            f"/api/stream/{item_id}/hls/{session_id}/segment_00000.m4s"
        )
        assert res_seg.status_code == 200
        assert res_seg.data == b"segment bytes"

        # 7. POST /api/stream/<id>/hls/<session_id>/heartbeat
        res_hb = client.post(f"/api/stream/{item_id}/hls/{session_id}/heartbeat")
        assert res_hb.status_code == 200
        assert res_hb.get_json()["status"] == "ok"

        # 8. POST /api/stream/<id>/hls/<session_id>/stop
        res_stop = client.post(f"/api/stream/{item_id}/hls/{session_id}/stop")
        assert res_stop.status_code == 200
        assert res_stop.get_json()["status"] == "stopped"

        # Verify session is cleaned up
        assert transcode_supervisor.get_session(session_id) is None
