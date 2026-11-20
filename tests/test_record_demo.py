"""On-board recording: the Pi2 records its own virtual display at full speed (no time compression)."""
from bench import record_demo


def test_board_recording_command_captures_the_virtual_display_in_real_time():
    command = record_demo.board_record_command(":94", "/tmp/rec/x.mp4", fps=15, width=1080)
    assert "x11grab" in command and "-i :94" in command and "-framerate 15" in command
    assert "scale=1080:-2:flags=fast_bilinear" in command and "/tmp/rec/x.mp4" in command
    assert "setpts" not in command and "-r " not in command         # nothing speeds the video up
    assert command.startswith("mkdir -p /tmp/rec;")                  # output folder exists first
    assert "X94" in command                                          # waits until the display is up


def test_board_recording_stays_off_the_cluster_core():
    command = record_demo.board_record_command(":94", "/tmp/rec/x.mp4")
    assert "nice -n 19" in command and "taskset -c 1-3" in command   # the cluster owns core 0 (its latency is measured)


def test_stopping_asks_ffmpeg_to_finish_the_file_cleanly():
    command = record_demo.board_record_stop_command()
    assert "-INT" in command and "ffmpeg" in command


def test_board_recording_avoids_the_hardware_h264_encoder_that_stalled_under_load():
    command = record_demo.board_record_command(":94", "/tmp/rec/x.mkv")
    assert "-c:v mjpeg" in command and "h264_v4l2m2m" not in command   # stalled -> black frames (2026-11-20)


def test_the_raw_board_file_is_turned_into_an_mp4_on_the_laptop(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg missing")
    raw = tmp_path / "raw.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=5", "-t", "2",
                    "-c:v", "mjpeg", str(raw)], check=True)
    out = record_demo.transcode(raw, tmp_path / "video.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "h264"


def test_recording_starts_only_after_the_run_has_restarted_the_virtual_display():
    # The run stops and recreates Xvfb :94; a recorder attached earlier keeps a dead connection and films black.
    ready = "[screen] cockpit feeder + monitoring screen started on Pi2's virtual display :94 (no monitor attached)"
    assert record_demo.screen_ready(ready)
    assert not record_demo.screen_ready("[services] cluster(core 0) + IVI V2 on Pi2, Pi3 monitor, Pi1 drive profile started")
    assert not record_demo.screen_ready("[screen] cockpit feeder + fullscreen monitoring screen started on Pi2's HDMI monitor")



def test_recording_ends_when_the_run_reaches_its_final_state_before_the_boards_are_shut_down():
    # After "[final]" the run stops the cluster and the IVI: recording on would film a dead screen at the end.
    assert record_demo.recording_done('[final] status={\'phase\': \'stable\'}')
    assert not record_demo.recording_done('[vulnerable_outcome] settled=True')
    assert not record_demo.recording_done('[reattack_through_closed_gate] response=ERROR gateway closed')
