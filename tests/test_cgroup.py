"""Reading the systemd unit a process belongs to.

On a systemd machine this answers "what is this thing part of" better than
the parent PID does, so it is worth being right about both cgroup layouts.
"""

from tanksmanager.backend.sampler import _cgroup_unit

V2_SERVICE = "0::/system.slice/NetworkManager.service\n"
V2_SCOPE = ("0::/user.slice/user-1000.slice/user@1000.service/"
            "app.slice/app-thunar-1234.scope\n")
V1 = ("12:pids:/system.slice/sshd.service\n"
      "3:memory:/system.slice/sshd.service\n"
      "1:name=systemd:/system.slice/sshd.service\n")


def test_cgroup_v2_service(fake_tree):
    fake_tree.write("proc/900/cgroup", V2_SERVICE)

    assert _cgroup_unit(900, fake_tree.path("proc")) == "NetworkManager.service"


def test_cgroup_v2_user_scope(fake_tree):
    fake_tree.write("proc/900/cgroup", V2_SCOPE)

    assert _cgroup_unit(900, fake_tree.path("proc")) == "app-thunar-1234.scope"


def test_cgroup_v1_uses_the_name_systemd_controller(fake_tree):
    fake_tree.write("proc/900/cgroup", V1)

    assert _cgroup_unit(900, fake_tree.path("proc")) == "sshd.service"


def test_a_process_in_the_root_cgroup_has_no_unit(fake_tree):
    # Kernel threads live here, and so does anything started outside systemd.
    fake_tree.write("proc/900/cgroup", "0::/\n")

    assert _cgroup_unit(900, fake_tree.path("proc")) == ""


def test_a_leaf_that_is_not_a_unit_is_not_reported(fake_tree):
    fake_tree.write("proc/900/cgroup", "0::/some/container/path\n")

    assert _cgroup_unit(900, fake_tree.path("proc")) == ""


def test_a_process_that_exited_is_not_an_error(fake_tree):
    assert _cgroup_unit(900, fake_tree.path("proc")) == ""


def test_malformed_lines_are_skipped(fake_tree):
    fake_tree.write("proc/900/cgroup", "garbage\n0::/system.slice/cron.service\n")

    assert _cgroup_unit(900, fake_tree.path("proc")) == "cron.service"
