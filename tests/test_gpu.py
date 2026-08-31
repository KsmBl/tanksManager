"""DRM fdinfo parsing and the per-process GPU figure.

The kernel exposes GPU work as monotonically rising per-engine nanosecond
counters, once per open file description.  Turning that into a percentage
means de-duplicating the clients and differencing against the last pass,
which is where the interesting mistakes live.
"""

from tanksmanager.backend import gpu

FDINFO = """\
pos:\t0
flags:\t02100002
drm-driver:\tamdgpu
drm-pdev:\t0000:03:00.0
drm-client-id:\t{client}
drm-engine-gfx:\t{gfx} ns
drm-engine-compute:\t{compute} ns
drm-resident-vram:\t{vram} KiB
"""


def _client(tree, pid, fd, client, gfx=0, compute=0, vram=0):
    tree.write(f"proc/{pid}/fdinfo/{fd}",
               FDINFO.format(client=client, gfx=gfx, compute=compute, vram=vram))


def test_engine_counters_are_summed_per_device(fake_tree):
    _client(fake_tree, 100, 3, client=1, gfx=5_000_000)
    _client(fake_tree, 200, 3, client=2, gfx=7_000_000)

    per_dev, _ = gpu.scan_fdinfo(fake_tree.path("proc"))

    assert per_dev["0000:03:00.0"]["engines"]["gfx"] == 12_000_000
    assert per_dev["0000:03:00.0"]["clients"] == 2


def test_descriptors_sharing_a_client_id_are_counted_once(fake_tree):
    # Two fds onto the same DRM client each report the same running totals.
    # Summing them blind would double every figure on the card.
    _client(fake_tree, 100, 3, client=7, gfx=5_000_000)
    _client(fake_tree, 100, 4, client=7, gfx=5_000_000)

    per_dev, per_pid = gpu.scan_fdinfo(fake_tree.path("proc"))

    assert per_dev["0000:03:00.0"]["engines"]["gfx"] == 5_000_000
    assert per_dev["0000:03:00.0"]["clients"] == 1
    assert per_pid[100]["gfx"] == 5_000_000


def test_per_pid_totals_are_reported_separately(fake_tree):
    _client(fake_tree, 100, 3, client=1, gfx=4_000_000, compute=1_000_000)
    _client(fake_tree, 200, 3, client=2, gfx=9_000_000)

    _, per_pid = gpu.scan_fdinfo(fake_tree.path("proc"))

    assert per_pid[100] == {"gfx": 4_000_000, "compute": 1_000_000}
    assert per_pid[200] == {"gfx": 9_000_000, "compute": 0}


def test_descriptors_without_drm_engines_are_ignored(fake_tree):
    fake_tree.write("proc/100/fdinfo/3", "pos:\t0\nflags:\t02\n")

    assert gpu.scan_fdinfo(fake_tree.path("proc")) == ({}, {})


def test_non_numeric_directories_in_proc_are_skipped(fake_tree):
    fake_tree.write("proc/self/fdinfo/3", FDINFO.format(
        client=1, gfx=1, compute=0, vram=0))

    assert gpu.scan_fdinfo(fake_tree.path("proc")) == ({}, {})


# -- turning counters into a percentage -------------------------------------

def test_busy_percent_is_the_busiest_engine_not_the_sum():
    # A process keeping two engines busy at once must not read over 100 %,
    # and the column has to stay comparable with the card's own figure.
    sampler = gpu.GpuSampler.__new__(gpu.GpuSampler)
    sampler._prev_proc = {42: {"gfx": 0, "compute": 0}}

    sampler._update_proc_busy({42: {"gfx": 800_000_000, "compute": 900_000_000}}, 1.0)

    assert sampler.proc_busy[42] == 90.0


def test_a_reset_counter_does_not_produce_a_negative_reading():
    # Counters restart when a client exits and its id is reused.
    sampler = gpu.GpuSampler.__new__(gpu.GpuSampler)
    sampler._prev_proc = {42: {"gfx": 900_000_000}}

    sampler._update_proc_busy({42: {"gfx": 1_000_000}}, 1.0)

    assert 42 not in sampler.proc_busy


def test_readings_are_capped_at_one_hundred_percent():
    sampler = gpu.GpuSampler.__new__(gpu.GpuSampler)
    sampler._prev_proc = {}

    sampler._update_proc_busy({42: {"gfx": 5_000_000_000}}, 1.0)

    # First sight of a process has no baseline, so it reads zero rather than
    # attributing every nanosecond since boot to this one second.
    assert sampler.proc_busy == {}


def test_idle_processes_are_left_out_of_the_map():
    sampler = gpu.GpuSampler.__new__(gpu.GpuSampler)
    sampler._prev_proc = {42: {"gfx": 1_000}}

    sampler._update_proc_busy({42: {"gfx": 1_000}}, 1.0)

    assert sampler.proc_busy == {}
