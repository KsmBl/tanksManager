"""Tank Mode: shell the charts.

Options > Tank Mode turns every history graph into a firing range.  A tank
parks itself on the baseline, and clicking anywhere on the plate lays the
barrel onto that point and puts a round through it: muzzle flash, a shell on
a ballistic arc, an airburst of fire and debris, a crater blown clean
through the graph, and the trace left burning at the point of impact until
it goes out.

None of it touches the data.  Everything here is painted over the finished
graph and expires on its own, so a chart that is on fire is still a chart
you can read.

The whole thing animates from one timer that only runs while something is
still alight, and stops the moment the last ember dies - a screen full of
craters costs nothing once the fires are out.
"""

from __future__ import annotations

import math
import random
import time

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

FRAME_MS = 16                       # 60 fps while anything is burning

FLIGHT_TIME = 0.42                  # seconds from muzzle to target
MUZZLE_FLASH = 0.13
BLAST_TIME = 0.55
SHOCKWAVE_TIME = 0.42
DEBRIS_TIME = 1.5
BURN_TIME = 11.0                    # how long the trace stays alight
SMOULDER_TIME = 4.0                 # embers cooling in the crater afterwards
CRATER_LIFE = 45.0                  # craters scab over eventually

MAX_CRATERS = 14
MAX_FIRES = 8

# Fire is the same colour whatever the desktop theme is doing: on the XP
# plate it wants to read as heat against the green, not as another series.
EMBER = (1.0, 0.36, 0.06)
FLAME_DEEP = (0.85, 0.16, 0.02)
FLAME_MID = (1.0, 0.48, 0.05)
FLAME_HOT = (1.0, 0.85, 0.35)
FLAME_CORE = (1.0, 0.98, 0.86)
SMOKE = (0.30, 0.29, 0.28)
STEEL = (0.32, 0.36, 0.30)
STEEL_LIT = (0.50, 0.55, 0.45)
STEEL_DARK = (0.16, 0.19, 0.15)


def _ease_out(t):
    return 1.0 - (1.0 - t) ** 3


def _flicker(t, seed, speed=11.0):
    """Layered sines - a candle flame's wobble without a per-frame random,
    which would look like static rather than like burning."""
    return (0.62
            + 0.24 * math.sin(t * speed + seed)
            + 0.10 * math.sin(t * speed * 2.31 + seed * 1.7)
            + 0.06 * math.sin(t * speed * 4.13 + seed * 2.9))


class Shell:
    """A round in flight, on a quadratic arc from the muzzle to the target."""

    __slots__ = ("x0", "y0", "x1", "y1", "born", "arc", "spin")

    def __init__(self, x0, y0, x1, y1, now):
        self.x0, self.y0 = x0, y0
        self.x1, self.y1 = x1, y1
        self.born = now
        # Lob it: the further the shot, the higher the arc.
        self.arc = min(90.0, 26.0 + math.hypot(x1 - x0, y1 - y0) * 0.34)
        self.spin = random.uniform(-1.0, 1.0)

    def age(self, now):
        return now - self.born

    def position(self, t):
        """Quadratic Bezier through a control point lifted above the midpoint."""
        cx = (self.x0 + self.x1) * 0.5
        cy = min(self.y0, self.y1) - self.arc
        u = 1.0 - t
        x = u * u * self.x0 + 2 * u * t * cx + t * t * self.x1
        y = u * u * self.y0 + 2 * u * t * cy + t * t * self.y1
        return x, y


class Blast:
    """The moment of impact: flash, fireball, shockwave, debris and sparks."""

    def __init__(self, x, y, now, radius):
        self.x, self.y = x, y
        self.born = now
        self.radius = radius
        rng = random.Random(int(x * 7919 + y * 104729))
        self.debris = []
        for _ in range(22):
            angle = rng.uniform(-math.pi * 0.96, -math.pi * 0.04)
            speed = rng.uniform(70.0, 260.0)
            self.debris.append((
                math.cos(angle) * speed,        # vx
                math.sin(angle) * speed,        # vy
                rng.uniform(1.0, 2.8),          # size
                rng.uniform(0.55, 1.0),         # lifetime scale
                rng.random(),                   # ember vs metal
            ))
        self.sparks = []
        for _ in range(26):
            angle = rng.uniform(0, math.tau)
            speed = rng.uniform(180.0, 520.0)
            self.sparks.append((math.cos(angle) * speed,
                                math.sin(angle) * speed,
                                rng.uniform(0.25, 0.5)))

    def age(self, now):
        return now - self.born

    def dead(self, now):
        return self.age(now) > DEBRIS_TIME


class Crater:
    """A hole blown through the plate, with a charred rim that cools."""

    def __init__(self, x, y, now, radius):
        self.x, self.y = x, y
        self.born = now
        self.radius = radius
        rng = random.Random(int(x * 31 + y * 17 + radius))
        # A stable jagged outline - regenerating it per frame would make the
        # crater crawl, which reads as noise rather than as damage.
        points = 15
        self.rim = []
        for i in range(points):
            angle = math.tau * i / points
            jitter = rng.uniform(0.72, 1.24)
            self.rim.append((angle, jitter))
        self.scorch = [(rng.uniform(0, math.tau), rng.uniform(1.25, 2.1),
                        rng.uniform(0.5, 1.0)) for _ in range(9)]
        self.embers = [(rng.uniform(0, math.tau), rng.uniform(0.15, 0.85),
                        rng.uniform(0, math.tau)) for _ in range(7)]

    def age(self, now):
        return now - self.born

    def dead(self, now):
        return self.age(now) > CRATER_LIFE

    def path(self, cr, scale=1.0):
        first = True
        pts = []
        for angle, jitter in self.rim:
            r = self.radius * jitter * scale
            pts.append((self.x + math.cos(angle) * r,
                        self.y + math.sin(angle) * r * 0.82))
        # Close the ring smoothly through the midpoints of each edge, which
        # keeps the outline ragged without turning it into a starburst.
        n = len(pts)
        for i in range(n):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            mx, my = (ax + bx) * 0.5, (ay + by) * 0.5
            if first:
                cr.move_to(mx, my)
                first = False
            else:
                cr.curve_to(ax, ay, ax, ay, mx, my)
        cr.close_path()


class Fire:
    """The trace, alight. Sits wherever the round landed and burns out."""

    def __init__(self, x, y, now, width):
        self.x, self.y = x, y
        self.floor = y                  # never climbs above the impact point
        self.born = now
        self.width = width
        rng = random.Random(int(x * 6151 + y * 389))
        count = max(4, int(width / 7))
        self.tongues = []
        for i in range(count):
            # Spread across the burning span, tallest in the middle.
            u = (i + 0.5) / count
            centre = 1.0 - abs(u - 0.5) * 1.7
            self.tongues.append((
                (u - 0.5) * width,              # offset from centre
                max(0.25, centre),              # height scale
                rng.uniform(0, math.tau),       # phase
                rng.uniform(0.85, 1.2),         # speed scale
            ))
        self.embers = [(rng.uniform(-0.5, 0.5), rng.uniform(0, 1.0),
                        rng.uniform(0.5, 1.0), rng.uniform(0, math.tau))
                       for _ in range(10)]
        self.smoke = [(rng.uniform(-0.35, 0.35), rng.uniform(0, 1.0),
                       rng.uniform(0.7, 1.4)) for _ in range(7)]

    def age(self, now):
        return now - self.born

    def dead(self, now):
        return self.age(now) > BURN_TIME + SMOULDER_TIME

    def intensity(self, now):
        """1 while it rages, easing to 0 as it burns out."""
        a = self.age(now)
        if a < 0.35:
            return a / 0.35                     # catching light
        if a < BURN_TIME * 0.6:
            return 1.0
        remaining = (BURN_TIME - a) / (BURN_TIME * 0.4)
        return max(0.0, min(1.0, remaining))


class Battlefield:
    """Everything currently happening to one graph."""

    def __init__(self, widget, trace_y=None):
        self.widget = widget
        # Given a pixel x, where the topmost series is drawn right now.  The
        # fires re-anchor to it every frame, so a burning stretch of chart
        # rides the line up and down instead of hanging in mid air.
        self.trace_y = trace_y
        self.shells = []
        self.blasts = []
        self.craters = []
        self.fires = []
        self.recoil = 0.0
        self.fired_at = -99.0
        self.aim = None                 # last target, so the barrel stays put
        self._timer = 0
        self._rounds = 0

    # -- lifecycle ----------------------------------------------------------
    def busy(self):
        return bool(self.shells or self.blasts or self.fires or self.craters)

    def clear(self):
        self.shells.clear()
        self.blasts.clear()
        self.craters.clear()
        self.fires.clear()
        self.aim = None
        self._stop()
        self.widget.queue_draw()

    def _start(self):
        if not self._timer:
            self._timer = GLib.timeout_add(FRAME_MS, self._tick)

    def _stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self):
        now = time.monotonic()
        self._expire(now)
        self.widget.queue_draw()
        if not self.busy():
            self._timer = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _expire(self, now):
        self.blasts = [b for b in self.blasts if not b.dead(now)]
        self.craters = [c for c in self.craters if not c.dead(now)]
        self.fires = [f for f in self.fires if not f.dead(now)]

    # -- firing -------------------------------------------------------------
    def tank_position(self, w, h):
        """Where the tank sits, and where its muzzle is when level."""
        return 26.0, h - 3.0

    def fire_at(self, tx, ty, w, h):
        now = time.monotonic()
        # The chart is the ground. A round aimed at empty sky still comes
        # down on the terrain below it, which is what keeps every crater
        # bitten out of the graph instead of hanging in the black.
        if self.trace_y is not None:
            ty = max(ty, self.trace_y(tx))
        ty = min(ty, h - 2.0)
        x0, y0 = self.tank_position(w, h)
        self.aim = (tx, ty)
        self.fired_at = now
        self.recoil = 1.0
        self._rounds += 1
        angle = math.atan2(ty - (y0 - 9.0), tx - x0)
        muzzle_x = x0 + math.cos(angle) * 22.0
        muzzle_y = (y0 - 9.0) + math.sin(angle) * 22.0
        self.shells.append(Shell(muzzle_x, muzzle_y, tx, ty, now))
        self._start()

    def _impact(self, shell, now, w, h):
        radius = max(11.0, min(30.0, h * 0.26))
        self.blasts.append(Blast(shell.x1, shell.y1, now, radius))
        self.craters.append(Crater(shell.x1, shell.y1, now, radius))
        # The crater goes where the round landed; the fire goes on the line,
        # because a burning chart is the point of the exercise.
        self.fires.append(Fire(shell.x1, shell.y1, now, radius * 2.1))
        del self.craters[:-MAX_CRATERS]
        del self.fires[:-MAX_FIRES]

    # -- drawing ------------------------------------------------------------
    def draw_damage(self, cr, w, h, now=None):
        """Craters and scorching: painted over the finished graph so the hole
        goes through the data, not behind it."""
        now = now or time.monotonic()
        for crater in self.craters:
            self._draw_crater(cr, crater, now)

    def draw_fire(self, cr, w, h, now=None):
        """Flame, embers and smoke, then anything still in the air."""
        now = now or time.monotonic()
        cr.save()
        cr.rectangle(0, 0, w, h)
        cr.clip()
        for fire in self.fires:
            if self.trace_y is not None:
                # Ride the line as the data scrolls underneath, so a burning
                # stretch of chart stays on the chart.
                fire.y = max(self.trace_y(fire.x), fire.floor)
            self._draw_fire(cr, fire, now)
        for blast in self.blasts:
            self._draw_blast(cr, blast, now)
        for shell in list(self.shells):
            t = shell.age(now) / FLIGHT_TIME
            if t >= 1.0:
                self.shells.remove(shell)
                self._impact(shell, now, w, h)
                continue
            self._draw_shell(cr, shell, t, now)
        self._draw_tank(cr, w, h, now)
        cr.restore()

    # -- pieces -------------------------------------------------------------
    def _draw_crater(self, cr, crater, now):
        age = crater.age(now)
        fade = 1.0 - max(0.0, (age - CRATER_LIFE * 0.6) / (CRATER_LIFE * 0.4))
        fade = max(0.0, min(1.0, fade))
        if fade <= 0.0:
            return
        heat = max(0.0, 1.0 - age / SMOULDER_TIME)

        # Scorching sprayed outwards, under the hole itself.
        cr.save()
        for angle, reach, weight in crater.scorch:
            r = crater.radius * reach
            grad = cairo.RadialGradient(crater.x, crater.y, 0,
                                        crater.x, crater.y, r)
            grad.add_color_stop_rgba(0.0, 0.05, 0.03, 0.02, 0.55 * fade * weight)
            grad.add_color_stop_rgba(1.0, 0.05, 0.03, 0.02, 0.0)
            cr.set_source(grad)
            cr.move_to(crater.x, crater.y)
            cr.arc(crater.x, crater.y, r, angle - 0.42, angle + 0.42)
            cr.close_path()
            cr.fill()
        cr.restore()

        # The hole: black, because the plate below the graph is black.
        crater.path(cr)
        cr.set_source_rgba(0.02, 0.02, 0.02, 0.97 * fade)
        cr.fill_preserve()

        # Charred lip, still glowing while it is fresh.
        cr.set_line_width(2.4)
        cr.set_source_rgba(0.22 + 0.62 * heat, 0.09 + 0.22 * heat,
                           0.05, (0.85 + 0.15 * heat) * fade)
        cr.stroke()

        # Spoil thrown up around the lip: a couple of chunks of the graph
        # that did not land far, which is what sells it as a hole rather
        # than a smudge.
        for angle, reach, weight in crater.scorch[:5]:
            cx = crater.x + math.cos(angle) * crater.radius * (0.9 + reach * 0.12)
            cy = crater.y + math.sin(angle) * crater.radius * 0.55
            cr.set_source_rgba(0.10, 0.08, 0.05, 0.8 * fade)
            cr.arc(cx, cy, 1.3 + weight * 1.4, 0, math.tau)
            cr.fill()

        if heat > 0.01:
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            crater.path(cr, 1.06)
            grad = cairo.RadialGradient(crater.x, crater.y, crater.radius * 0.35,
                                        crater.x, crater.y, crater.radius * 1.25)
            grad.add_color_stop_rgba(0.0, *EMBER, 0.0)
            grad.add_color_stop_rgba(0.75, *EMBER, 0.42 * heat * fade)
            grad.add_color_stop_rgba(1.0, *EMBER, 0.0)
            cr.set_source(grad)
            cr.fill()
            # Individual coals winking in the bottom of the hole.
            for angle, dist, phase in crater.embers:
                pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 6.0 + phase))
                ex = crater.x + math.cos(angle) * crater.radius * dist
                ey = crater.y + math.sin(angle) * crater.radius * dist * 0.7
                cr.set_source_rgba(*FLAME_MID, 0.9 * heat * pulse * fade)
                cr.arc(ex, ey, 1.1, 0, math.tau)
                cr.fill()
            cr.restore()

    def _draw_fire(self, cr, fire, now):
        power = fire.intensity(now)
        age = fire.age(now)
        if power <= 0.005:
            # Burnt out: a last thread of smoke.
            self._draw_smoke(cr, fire, now, 0.25 * max(
                0.0, 1.0 - (age - BURN_TIME) / SMOULDER_TIME))
            return

        self._draw_smoke(cr, fire, now, 0.5 + 0.5 * power)

        base_height = 30.0 + fire.width * 0.62

        # Char the line first. Flame painted straight onto a saturated green
        # fill adds up to yellow and stops reading as fire at all, so the
        # stretch that is burning is blackened before anything is set alight
        # on top of it.
        cr.save()
        char_w = fire.width * 0.72
        char_h = max(4.0, fire.width * 0.30)
        grad = cairo.RadialGradient(fire.x, fire.y, 0, fire.x, fire.y, char_w)
        char_alpha = 0.92 * min(1.0, 0.35 + power)
        grad.add_color_stop_rgba(0.0, 0.04, 0.03, 0.02, char_alpha)
        grad.add_color_stop_rgba(0.62, 0.05, 0.03, 0.02, char_alpha * 0.8)
        grad.add_color_stop_rgba(1.0, 0.05, 0.03, 0.02, 0.0)
        cr.set_source(grad)
        cr.save()
        cr.translate(fire.x, fire.y)
        cr.scale(1.0, char_h / char_w)
        cr.arc(0, 0, char_w, 0, math.tau)
        cr.restore()
        cr.fill()
        cr.restore()

        # The flames themselves blend normally, so they keep their own
        # colour whatever they are burning on top of.
        cr.save()
        for offset, scale, phase, speed in fire.tongues:
            wobble = _flicker(now, phase, 9.5 * speed)
            height = base_height * scale * power * wobble
            if height < 1.5:
                continue
            x = fire.x + offset
            y = fire.y
            sway = math.sin(now * 2.6 * speed + phase) * height * 0.16
            half = max(2.0, height * 0.21)

            # Three nested tongues: deep red outside, yellow-white core.
            for layer, colour, shrink, alpha in (
                    (0, FLAME_DEEP, 1.0, 0.80),
                    (1, FLAME_MID, 0.66, 0.85),
                    (2, FLAME_HOT, 0.34, 0.90)):
                lh = height * (1.0 - layer * 0.19)
                lw = half * shrink
                tip_x = x + sway * (1.0 + layer * 0.25)
                tip_y = y - lh
                grad = cairo.LinearGradient(x, y, tip_x, tip_y)
                grad.add_color_stop_rgba(0.0, *colour, alpha * power)
                grad.add_color_stop_rgba(0.45, *colour, alpha * power * 0.85)
                grad.add_color_stop_rgba(0.80, *colour, alpha * power * 0.35)
                grad.add_color_stop_rgba(1.0, *colour, 0.0)
                cr.set_source(grad)
                cr.move_to(x - lw, y)
                cr.curve_to(x - lw * 0.9, y - lh * 0.45,
                            tip_x - lw * 0.55, y - lh * 0.75,
                            tip_x, tip_y)
                cr.curve_to(tip_x + lw * 0.55, y - lh * 0.75,
                            x + lw * 0.9, y - lh * 0.45,
                            x + lw, y)
                cr.close_path()
                cr.fill()

            # White-hot root where the trace itself is alight.
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            grad = cairo.RadialGradient(x, y, 0, x, y, half * 1.5)
            grad.add_color_stop_rgba(0.0, *FLAME_CORE, 0.55 * power)
            grad.add_color_stop_rgba(0.45, *FLAME_MID, 0.30 * power)
            grad.add_color_stop_rgba(1.0, *FLAME_MID, 0.0)
            cr.set_source(grad)
            cr.arc(x, y, half * 1.5, 0, math.tau)
            cr.fill()
            cr.restore()

        # Embers lifting off the fire.
        cr.set_operator(cairo.OPERATOR_ADD)
        for dx, speed, size, phase in fire.embers:
            travel = ((now * (0.35 + speed * 0.4) + phase) % 1.0)
            ex = fire.x + dx * fire.width + math.sin(
                travel * 7.0 + phase) * 5.0
            ey = fire.y - travel * (base_height * 1.5)
            alpha = (1.0 - travel) * power * 0.9
            cr.set_source_rgba(*FLAME_MID, alpha)
            cr.arc(ex, ey, size * 0.9, 0, math.tau)
            cr.fill()
        cr.restore()

    def _draw_smoke(self, cr, fire, now, weight):
        if weight <= 0.01:
            return
        cr.save()
        base_height = 34.0 + fire.width * 0.7
        for dx, speed, size in fire.smoke:
            travel = ((now * (0.18 + speed * 0.12) + dx * 3.0) % 1.0)
            sx = fire.x + dx * fire.width + math.sin(travel * 3.4 + dx * 9) * 9.0
            sy = fire.y - travel * base_height * 2.1
            radius = (3.0 + travel * 13.0) * size
            alpha = (1.0 - travel) * 0.20 * weight
            grad = cairo.RadialGradient(sx, sy, 0, sx, sy, radius)
            grad.add_color_stop_rgba(0.0, *SMOKE, alpha)
            grad.add_color_stop_rgba(1.0, *SMOKE, 0.0)
            cr.set_source(grad)
            cr.arc(sx, sy, radius, 0, math.tau)
            cr.fill()
        cr.restore()

    def _draw_shell(self, cr, shell, t, now):
        x, y = shell.position(t)
        px, py = shell.position(max(0.0, t - 0.075))
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)

        # Tracer trail.
        grad = cairo.LinearGradient(px, py, x, y)
        grad.add_color_stop_rgba(0.0, *FLAME_MID, 0.0)
        grad.add_color_stop_rgba(1.0, *FLAME_HOT, 0.85)
        cr.set_source(grad)
        cr.set_line_width(2.6)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(px, py)
        cr.line_to(x, y)
        cr.stroke()

        # The round itself, pointing where it is going.
        angle = math.atan2(y - py, x - px)
        cr.translate(x, y)
        cr.rotate(angle)
        cr.set_source_rgba(*FLAME_CORE, 0.95)
        cr.move_to(4.2, 0)
        cr.line_to(-3.0, 2.0)
        cr.line_to(-3.0, -2.0)
        cr.close_path()
        cr.fill()
        grad = cairo.RadialGradient(0, 0, 0, 0, 0, 9.0)
        grad.add_color_stop_rgba(0.0, *FLAME_HOT, 0.55)
        grad.add_color_stop_rgba(1.0, *FLAME_HOT, 0.0)
        cr.set_source(grad)
        cr.arc(0, 0, 9.0, 0, math.tau)
        cr.fill()
        cr.restore()

    def _draw_blast(self, cr, blast, now):
        age = blast.age(now)
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)

        # Fireball.
        if age < BLAST_TIME:
            t = age / BLAST_TIME
            radius = blast.radius * (0.45 + 2.3 * _ease_out(t))
            alpha = (1.0 - t) ** 1.6
            grad = cairo.RadialGradient(blast.x, blast.y, 0,
                                        blast.x, blast.y, radius)
            grad.add_color_stop_rgba(0.0, *FLAME_CORE, 0.95 * alpha)
            grad.add_color_stop_rgba(0.22, *FLAME_HOT, 0.85 * alpha)
            grad.add_color_stop_rgba(0.55, *FLAME_MID, 0.55 * alpha)
            grad.add_color_stop_rgba(0.82, *FLAME_DEEP, 0.28 * alpha)
            grad.add_color_stop_rgba(1.0, *FLAME_DEEP, 0.0)
            cr.set_source(grad)
            cr.arc(blast.x, blast.y, radius, 0, math.tau)
            cr.fill()

        # Shockwave.
        if age < SHOCKWAVE_TIME:
            t = age / SHOCKWAVE_TIME
            radius = blast.radius * (0.6 + 4.4 * _ease_out(t))
            cr.set_line_width(max(0.6, 2.6 * (1.0 - t)))
            cr.set_source_rgba(*FLAME_HOT, 0.30 * (1.0 - t) ** 2.4)
            cr.arc(blast.x, blast.y, radius, 0, math.tau)
            cr.stroke()

        # Sparks: fast, thin, gone almost at once.
        for vx, vy, life in blast.sparks:
            if age > life:
                continue
            t = age / life
            sx = blast.x + vx * age
            sy = blast.y + vy * age + 240.0 * age * age
            cr.set_source_rgba(*FLAME_CORE, (1.0 - t) * 0.8)
            cr.set_line_width(1.2)
            cr.move_to(sx, sy)
            cr.line_to(sx - vx * 0.012, sy - vy * 0.012)
            cr.stroke()

        # Debris arcing away under gravity.
        for vx, vy, size, life, kind in blast.debris:
            span = DEBRIS_TIME * life
            if age > span:
                continue
            t = age / span
            dx = blast.x + vx * age
            dy = blast.y + vy * age + 420.0 * age * age
            alpha = (1.0 - t) ** 1.4
            if kind > 0.45:
                cr.set_source_rgba(*EMBER, alpha)
            else:
                cr.set_source_rgba(0.25, 0.22, 0.20, alpha)
            cr.save()
            cr.translate(dx, dy)
            cr.rotate(age * 9.0 * (1.0 if kind > 0.5 else -1.0))
            cr.rectangle(-size, -size * 0.7, size * 2, size * 1.4)
            cr.fill()
            cr.restore()
        cr.restore()

    def _draw_tank(self, cr, w, h, now):
        x, y = self.tank_position(w, h)
        since = now - self.fired_at
        recoil = 0.0
        if 0.0 <= since < 0.34:
            # Kick back hard, settle back slowly.
            recoil = math.exp(-since * 11.0) * math.cos(since * 26.0) * 5.0

        target = self.aim or (w * 0.6, h * 0.35)
        pivot_y = y - 9.0
        angle = math.atan2(target[1] - pivot_y, max(1.0, target[0] - x))
        angle = max(-math.pi * 0.48, min(-0.02, angle))

        cr.save()
        cr.translate(x - recoil, y)

        # Shadow.
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.save()
        cr.scale(1.0, 0.3)
        cr.arc(0, 4, 19, 0, math.tau)
        cr.fill()
        cr.restore()

        # Barrel, from the turret pivot.
        cr.save()
        cr.translate(0, -9)
        cr.rotate(angle)
        cr.set_source_rgb(*STEEL_DARK)
        cr.rectangle(2, -2.6, 24, 5.2)
        cr.fill()
        cr.set_source_rgb(*STEEL)
        cr.rectangle(2, -2.0, 23, 3.4)
        cr.fill()
        cr.set_source_rgb(*STEEL_LIT)
        cr.rectangle(22, -3.0, 4, 6.0)      # muzzle brake
        cr.fill()

        if 0.0 <= since < MUZZLE_FLASH:
            t = since / MUZZLE_FLASH
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            length = 30.0 * (1.0 - t) + 8.0
            grad = cairo.LinearGradient(26, 0, 26 + length, 0)
            grad.add_color_stop_rgba(0.0, *FLAME_CORE, 0.95 * (1.0 - t))
            grad.add_color_stop_rgba(0.45, *FLAME_HOT, 0.65 * (1.0 - t))
            grad.add_color_stop_rgba(1.0, *FLAME_MID, 0.0)
            cr.set_source(grad)
            cr.move_to(26, 0)
            cr.line_to(26 + length, -7.5 * (1.0 - t) - 2)
            cr.line_to(26 + length, 7.5 * (1.0 - t) + 2)
            cr.close_path()
            cr.fill()
            cr.restore()
        cr.restore()

        # Hull.
        cr.set_source_rgb(*STEEL_DARK)
        cr.move_to(-20, 0)
        cr.line_to(20, 0)
        cr.line_to(18, -7)
        cr.line_to(-18, -7)
        cr.close_path()
        cr.fill()
        grad = cairo.LinearGradient(0, -7, 0, 0)
        grad.add_color_stop_rgb(0.0, *STEEL_LIT)
        grad.add_color_stop_rgb(1.0, *STEEL)
        cr.set_source(grad)
        cr.move_to(-19, -1)
        cr.line_to(19, -1)
        cr.line_to(17, -6.4)
        cr.line_to(-17, -6.4)
        cr.close_path()
        cr.fill()

        # Turret.
        cr.set_source_rgb(*STEEL)
        cr.move_to(-10, -7)
        cr.line_to(10, -7)
        cr.line_to(7, -14)
        cr.line_to(-8, -14)
        cr.close_path()
        cr.fill()
        cr.set_source_rgb(*STEEL_LIT)
        cr.rectangle(-8, -14, 15, 1.4)
        cr.fill()

        # Road wheels.
        cr.set_source_rgb(*STEEL_DARK)
        for wx in (-14, -7, 0, 7, 14):
            cr.arc(wx, 0, 3.2, 0, math.tau)
            cr.fill()
        cr.restore()
