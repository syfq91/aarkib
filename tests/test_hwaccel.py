from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from aarkib.services.settings_service import (
    get_effective_settings,
    update_settings,
)
from aarkib.services.transcoder import (
    TranscodeCapabilities,
    TranscodeProfile,
    TranscodeSupervisor,
    detect_transcode_capabilities,
    reset_transcode_cache,
    resolve_transcode_profile,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Ensure transcode capabilities cache is clean before and after each test."""
    reset_transcode_cache()
    yield
    reset_transcode_cache()


def test_transcode_capabilities_defaults():
    """Verify default TranscodeCapabilities model and serialization."""
    caps = TranscodeCapabilities()
    assert caps.software_available is True
    assert caps.vaapi_device is None
    assert caps.qsv_available is False
    assert caps.active_backend == "auto"
    assert caps.is_hardware_accelerated is False

    d = caps.to_dict()
    assert d["software_available"] is True
    assert d["vaapi_device"] is None
    assert d["qsv_available"] is False
    assert d["is_hardware_accelerated"] is False


def test_detect_transcode_capabilities_no_ffmpeg():
    """When FFmpeg binary is missing, all capabilities default to software False."""
    with patch("aarkib.services.transcoder.get_ffmpeg_binary", return_value=None):
        caps = detect_transcode_capabilities(force_refresh=True)
        assert caps.software_available is False
        assert caps.vaapi_device is None
        assert caps.qsv_available is False
        assert caps.is_hardware_accelerated is False


def test_detect_transcode_capabilities_vaapi_mocked(tmp_path):
    """Test successful discovery and validation of VA-API render node."""
    fake_node = tmp_path / "renderD128"
    fake_node.touch()

    def mock_run(cmd, *args, **kwargs):
        # VAAPI probe succeeds, QSV probe fails
        if "-vaapi_device" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=b"", stderr=b""
            )
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"")

    with (
        patch(
            "aarkib.services.transcoder.get_ffmpeg_binary",
            return_value="/usr/bin/ffmpeg",
        ),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("pathlib.Path.glob", return_value=[fake_node]),
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=True),
        patch("subprocess.run", side_effect=mock_run),
    ):
        caps = detect_transcode_capabilities(force_refresh=True)
        assert caps.software_available is True
        assert caps.vaapi_device == str(fake_node)
        assert caps.qsv_available is False
        assert caps.is_hardware_accelerated is True


def test_detect_transcode_capabilities_permission_denied(tmp_path):
    """If render node exists but permission is denied, fallback to software."""
    fake_node = tmp_path / "renderD128"
    fake_node.touch()

    with (
        patch(
            "aarkib.services.transcoder.get_ffmpeg_binary",
            return_value="/usr/bin/ffmpeg",
        ),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("pathlib.Path.glob", return_value=[fake_node]),
        patch("os.path.exists", return_value=True),
        patch("os.access", return_value=False),  # Permission denied
        patch(
            "subprocess.run", return_value=subprocess.CompletedProcess([], returncode=1)
        ),
    ):
        caps = detect_transcode_capabilities(force_refresh=True)
        assert caps.vaapi_device is None
        assert caps.qsv_available is False
        assert caps.is_hardware_accelerated is False


def test_detect_transcode_capabilities_qsv_mocked():
    """Test successful discovery and validation of Intel QSV encoder."""

    def mock_run(cmd, *args, **kwargs):
        # QSV probe succeeds, VAAPI probe fails
        if "h264_qsv" in cmd:
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout=b"", stderr=b""
            )
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"")

    with (
        patch(
            "aarkib.services.transcoder.get_ffmpeg_binary",
            return_value="/usr/bin/ffmpeg",
        ),
        patch("pathlib.Path.is_dir", return_value=False),
        patch("subprocess.run", side_effect=mock_run),
    ):
        caps = detect_transcode_capabilities(force_refresh=True)
        assert caps.vaapi_device is None
        assert caps.qsv_available is True
        assert caps.is_hardware_accelerated is True


def test_resolve_transcode_profile_selection():
    """Verify profile selection logic across auto, software, vaapi, and qsv."""
    hw_caps = TranscodeCapabilities(
        software_available=True,
        vaapi_device="/dev/dri/renderD128",
        qsv_available=True,
        active_backend="auto",
    )

    # 1. Auto selects VA-API when available
    prof_auto = resolve_transcode_profile(
        "auto", hw_caps, target_width=1280, bitrate_kbps=2500
    )
    assert prof_auto.backend == "vaapi"
    assert prof_auto.video_codec == "h264_vaapi"
    assert "-hwaccel" in prof_auto.hwaccel_args
    assert "/dev/dri/renderD128" in prof_auto.hwaccel_args
    assert "-vf" in prof_auto.filter_args

    # 2. Software explicitly requested
    prof_soft = resolve_transcode_profile(
        "software", hw_caps, target_width=1280, bitrate_kbps=2500
    )
    assert prof_soft.backend == "software"
    assert prof_soft.video_codec == "libx264"
    assert not prof_soft.hwaccel_args
    assert "-c:v" in prof_soft.encoder_args
    assert "libx264" in prof_soft.encoder_args

    # 3. QSV explicitly requested
    prof_qsv = resolve_transcode_profile(
        "qsv", hw_caps, target_width=1280, bitrate_kbps=2500
    )
    assert prof_qsv.backend == "qsv"
    assert prof_qsv.video_codec == "h264_qsv"
    assert "-c:v" in prof_qsv.encoder_args
    assert "h264_qsv" in prof_qsv.encoder_args

    # 4. Fallback when hardware is requested but not available
    soft_caps = TranscodeCapabilities(
        software_available=True,
        vaapi_device=None,
        qsv_available=False,
    )
    prof_fallback_vaapi = resolve_transcode_profile("vaapi", soft_caps)
    assert prof_fallback_vaapi.backend == "software"
    assert prof_fallback_vaapi.video_codec == "libx264"

    prof_fallback_qsv = resolve_transcode_profile("qsv", soft_caps)
    assert prof_fallback_qsv.backend == "software"
    assert prof_fallback_qsv.video_codec == "libx264"


def test_resolve_transcode_profile_argument_safety():
    """Verify all resolved argument lists are safe lists of strings (no shell=True)."""
    prof = resolve_transcode_profile("auto", target_width=1920, bitrate_kbps=4500)
    assert isinstance(prof, TranscodeProfile)
    assert isinstance(prof.hwaccel_args, tuple)
    assert isinstance(prof.filter_args, tuple)
    assert isinstance(prof.encoder_args, tuple)

    for arg in (*prof.hwaccel_args, *prof.filter_args, *prof.encoder_args):
        assert isinstance(arg, str)
        # Verify no shell chaining characters
        assert ";" not in arg
        assert "&&" not in arg
        assert "|" not in arg

    d = prof.to_dict()
    assert d["backend"] == prof.backend
    assert isinstance(d["encoder_args"], list)


def test_transcode_backend_settings_service(app):
    """Test dynamic administrator configuration of TRANSCODE_BACKEND via settings_service."""
    with app.app_context():
        # Read default
        eff = get_effective_settings(app)
        assert "TRANSCODE_BACKEND" in eff["settings"]
        assert eff["settings"]["TRANSCODE_BACKEND"]["value"] == "auto"

        # Update to vaapi
        res = update_settings(app, {"TRANSCODE_BACKEND": "vaapi"})
        assert res["settings"]["TRANSCODE_BACKEND"]["value"] == "vaapi"
        assert app.config["TRANSCODE_BACKEND"] == "vaapi"

        # Update to software
        res2 = update_settings(app, {"TRANSCODE_BACKEND": "software"})
        assert res2["settings"]["TRANSCODE_BACKEND"]["value"] == "software"
        assert app.config["TRANSCODE_BACKEND"] == "software"

        # Invalid choice rejected
        with pytest.raises(ValueError, match="must be one of"):
            update_settings(app, {"TRANSCODE_BACKEND": "nvenc_invalid"})


def test_transcode_supervisor_runtime_fallback(tmp_path):
    """Verify runtime fallback to CPU software encoding when hardware process crashes on startup."""
    supervisor = TranscodeSupervisor(idle_timeout=5.0)
    base_dir = tmp_path / "transcode_fallback"
    base_dir.mkdir(parents=True, exist_ok=True)

    dummy_file = tmp_path / "sample_video.mp4"
    dummy_file.write_bytes(b"dummy" * 50)

    # First spawn (hardware) exits with code 1 (failure)
    failing_proc = MagicMock()
    failing_proc.pid = 11111
    failing_proc.poll.return_value = 1
    failing_proc.returncode = 1

    # Second spawn (software fallback) succeeds and remains running
    success_proc = MagicMock()
    success_proc.pid = 22222
    success_proc.poll.return_value = None

    hw_caps = TranscodeCapabilities(
        software_available=True,
        vaapi_device="/dev/dri/renderD128",
        qsv_available=False,
    )

    with (
        patch(
            "aarkib.services.transcoder.detect_transcode_capabilities",
            return_value=hw_caps,
        ),
        patch(
            "aarkib.services.transcoder.get_ffmpeg_binary",
            return_value="/usr/bin/ffmpeg",
        ),
        patch(
            "subprocess.Popen", side_effect=[failing_proc, success_proc]
        ) as mock_popen,
    ):
        session = supervisor.create_or_get_hls_session(
            media_item_id=100,
            file_path=dummy_file,
            transcode_base_dir=base_dir,
            resolution="720p",
            backend="vaapi",
        )

        assert session is not None
        # Assert supervisor invoked fallback and assigned software backend
        assert session.backend == "software"
        assert session.vaapi_device is None
        assert mock_popen.call_count == 2
        # First call used VAAPI
        assert "-hwaccel" in mock_popen.call_args_list[0][0][0]
        # Second call used software libx264
        assert "libx264" in mock_popen.call_args_list[1][0][0]

    supervisor.cleanup_all()
