"""Swap, zram and zswap parsing.

These are the readings people most often disbelieve - drive swap and zram
look alike in every other tool - so the shapes they come back in are worth
pinning down.
"""

from tanksmanager.backend.sampler import _read_swaps, _read_zram, _zswap_params

SWAPS = """\
Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority
/dev/nvme0n1p3                          partition\t8388604\t\t262144\t\t-2
/swapfile                               file\t\t2097148\t\t0\t\t-3
/dev/zram0                              partition\t8388608\t\t4\t\t100
"""


def test_swap_sizes_are_converted_from_1k_units(fake_tree):
    path = fake_tree.write("swaps", SWAPS)
    devs = _read_swaps(path)

    assert [d.path for d in devs] == ["/dev/nvme0n1p3", "/swapfile", "/dev/zram0"]
    # /proc/swaps counts in 1024-byte units, not bytes.
    assert devs[0].size == 8388604 * 1024
    assert devs[0].used == 262144 * 1024
    assert devs[0].kind == "partition"
    assert devs[1].kind == "file"
    assert devs[0].priority == -2


def test_zram_devices_are_flagged_apart_from_drive_swap(fake_tree):
    devs = _read_swaps(fake_tree.write("swaps", SWAPS))
    kinds = {d.path: d.is_zram for d in devs}

    # The whole point of the distinction in the UI: a zram partition is not
    # a drive partition, even though /proc/swaps calls both "partition".
    assert kinds["/dev/zram0"] is True
    assert kinds["/dev/nvme0n1p3"] is False
    assert kinds["/swapfile"] is False


def test_missing_swaps_file_is_not_an_error():
    assert _read_swaps("/definitely/not/here") == []


def test_truncated_lines_are_skipped(fake_tree):
    path = fake_tree.write("swaps", "Filename Type Size Used Priority\n"
                                    "/dev/zram0 partition\n"
                                    "/swapfile file 100 10 -3\n")
    devs = _read_swaps(path)
    assert [d.path for d in devs] == ["/swapfile"]


def test_non_numeric_columns_are_skipped(fake_tree):
    path = fake_tree.write("swaps", "Filename Type Size Used Priority\n"
                                    "/dev/zram0 partition ? ? 100\n")
    assert _read_swaps(path) == []


# -- zram -------------------------------------------------------------------

def _zram_device(tree, name="zram0", disksize="8589934592",
                 mm_stat="4096 512 65536 0 65536 0 0 0 0",
                 algorithm="lzo lz4 [zstd] lz4hc"):
    tree.write(f"sys/block/{name}/disksize", disksize + "\n")
    if mm_stat is not None:
        tree.write(f"sys/block/{name}/mm_stat", mm_stat + "\n")
    tree.write(f"sys/block/{name}/comp_algorithm", algorithm + "\n")


def test_zram_reads_mm_stat_and_the_active_algorithm(fake_tree):
    _zram_device(fake_tree)
    swaps = _read_swaps(fake_tree.write("swaps",
        "Filename Type Size Used Priority\n/dev/zram0 partition 8388608 4 100\n"))

    devs = _read_zram(swaps, sys_block=fake_tree.path("sys/block"),
                      mounts_path=fake_tree.write("mounts", ""))

    assert len(devs) == 1
    dev = devs[0]
    assert dev.name == "zram0"
    assert dev.disksize == 8589934592
    assert (dev.orig, dev.compressed, dev.mem_used) == (4096, 512, 65536)
    # Only the bracketed entry is the one in use.
    assert dev.algorithm == "zstd"
    assert dev.used_as == "swap"


def test_zram_falls_back_to_the_individual_attributes(fake_tree):
    _zram_device(fake_tree, mm_stat=None)
    fake_tree.write("sys/block/zram0/orig_data_size", "2048\n")
    fake_tree.write("sys/block/zram0/compr_data_size", "700\n")
    fake_tree.write("sys/block/zram0/mem_used_total", "4096\n")

    devs = _read_zram([], sys_block=fake_tree.path("sys/block"),
                      mounts_path=fake_tree.write("mounts", ""))

    assert (devs[0].orig, devs[0].compressed, devs[0].mem_used) == (2048, 700, 4096)


def test_zram_used_as_a_filesystem_reports_its_mountpoint(fake_tree):
    _zram_device(fake_tree)
    mounts = fake_tree.write("mounts", "/dev/zram0 /var/log ext4 rw 0 0\n")

    devs = _read_zram([], sys_block=fake_tree.path("sys/block"), mounts_path=mounts)

    assert devs[0].used_as == "/var/log"


def test_unconfigured_zram_device_is_ignored(fake_tree):
    # The module is loaded and /sys/block/zram0 exists, but nobody sized it.
    _zram_device(fake_tree, disksize="0")

    devs = _read_zram([], sys_block=fake_tree.path("sys/block"),
                      mounts_path=fake_tree.write("mounts", ""))

    assert devs == []


# -- zswap ------------------------------------------------------------------

def test_zswap_enabled_and_compressor(fake_tree):
    fake_tree.write("zswap/enabled", "Y\n")
    fake_tree.write("zswap/compressor", "zstd\n")

    assert _zswap_params(fake_tree.path("zswap")) == (True, "zstd")


def test_zswap_disabled(fake_tree):
    fake_tree.write("zswap/enabled", "N\n")
    fake_tree.write("zswap/compressor", "lzo\n")

    assert _zswap_params(fake_tree.path("zswap")) == (False, "lzo")


def test_zswap_absent_from_the_kernel(fake_tree):
    assert _zswap_params(fake_tree.path("no-such-module")) == (False, "")
