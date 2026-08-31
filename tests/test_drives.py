"""Which /sys/block entries count as a drive.

This is the rule behind the hotplug behaviour in the Performance tab: a card
appears or disappears purely because `_detect` starts or stops listing the
device, so it is worth being explicit about what it accepts.
"""

import pytest

from tanksmanager.backend import drives


@pytest.fixture
def sysblock(fake_tree, monkeypatch):
    root = fake_tree.path("sys/block")
    fake_tree.write("sys/block/.keep", "")
    monkeypatch.setattr(drives, "SYS_BLOCK", root)
    return fake_tree


def add_disk(tree, name, sectors="1000215216", model="Samsung SSD 860",
             rotational="0", device=True):
    tree.write(f"sys/block/{name}/size", sectors + "\n")
    tree.write(f"sys/block/{name}/queue/rotational", rotational + "\n")
    if device:
        tree.write(f"sys/block/{name}/device/model", model + "\n")


def test_a_real_disk_is_detected(sysblock):
    add_disk(sysblock, "sda")

    found = drives.DriveSampler()._detect()

    assert list(found) == ["sda"]
    assert found["sda"]["model"] == "Samsung SSD 860"
    assert found["sda"]["size"] == 1000215216 * 512


def test_virtual_block_devices_are_excluded(sysblock):
    # No `device` symlink: loop, zram, ram and device-mapper nodes all look
    # like this, and none of them is a drive somebody can unplug.
    add_disk(sysblock, "loop0", device=False)
    add_disk(sysblock, "zram0", device=False)
    add_disk(sysblock, "dm-0", device=False)
    add_disk(sysblock, "sda")

    assert list(drives.DriveSampler()._detect()) == ["sda"]


def test_an_empty_card_reader_is_absent(sysblock):
    # A reader with no card in it reports zero sectors. Treating that as "not
    # there" is what makes inserting a card behave like plugging in a drive.
    add_disk(sysblock, "mmcblk0", sectors="0")

    assert drives.DriveSampler()._detect() == {}


def test_rotational_disks_are_reported_as_a_hard_disk(sysblock):
    add_disk(sysblock, "sda", rotational="1", model="WDC WD10EZEX")

    assert drives.DriveSampler()._detect()["sda"]["kind"] == "Hard disk"


def test_nvme_is_named_from_the_device_node(sysblock):
    add_disk(sysblock, "nvme0n1", model="WD_BLACK SN850X")

    assert drives.DriveSampler()._detect()["nvme0n1"]["kind"] == "NVMe SSD"


def test_counters_are_dropped_when_a_drive_disappears(sysblock, monkeypatch):
    # If the kernel later hands the same name to a different device, a stale
    # baseline would invent an enormous first delta for it.
    add_disk(sysblock, "sdb")
    sampler = drives.DriveSampler()
    monkeypatch.setattr(drives.psutil, "disk_io_counters", lambda perdisk: {})
    sampler.sample(1.0)
    sampler._prev["sdb"] = ("stale",)

    import os
    os.rename(sysblock.path("sys/block/sdb"), sysblock.path("sys/block/gone"))
    sampler.sample(1.0)

    assert "sdb" not in sampler._prev
