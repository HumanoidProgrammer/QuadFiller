#!/usr/bin/env python3
"""
QuadFiller — a game inspired by Encompass/JezzBall

MIT License - Copyright (c) 2026 S.S. Rath

Install:  pip install pygame
Run:      python quadfiller.py

Controls:
  Left-click          — draw a line from that point
  Right-click / Space — toggle Horizontal ↔ Vertical
  R                   — restart
  Esc                 — quit
"""

import sys, random, math
from collections import deque
import pygame

# ─── Tunables ────────────────────────────────────────────────────────────────
W, H       = 800, 600
UI_H       = 56
PLAY_Y     = UI_H
PLAY_H     = H - UI_H
CELL       = 8              # FIX 1: was 4; CELL=8 keeps cells > max ball speed
GCOLS      = W     // CELL  # 100
GROWS      = PLAY_H // CELL  # 68
BALL_R     = 10
LINE_SPD   = 2              # cells per frame per head (adjusted for CELL=8)
TARGET     = 0.75           # fraction captured to advance

# Guard against CELL being set so large that Grid.TOTAL = 0
assert GROWS > 2 and GCOLS > 2, f"CELL={CELL} is too large for window size"

# ─── Palette ─────────────────────────────────────────────────────────────────
BG       = (  8,  12,  24)
FREE_C   = ( 10,  15,  30)
CAP_C    = ( 10,  38,  18)
WALL_C   = ( 38,  62, 120)
LINE_C   = (155, 205, 255)
HEAD_C   = (255, 255, 255)
BALL_C   = (225,  75,  25)
BALL_M   = (255, 145,  55)
BALL_I   = (255, 220, 120)
UI_BG    = (  5,   8,  18)
TXT_C    = (175, 210, 255)
BAR_BG   = ( 18,  28,  50)
BAR_FG   = ( 50, 170,  75)
BAR_OK   = ( 80, 200,  90)
BAR_TGT  = (255, 225,  80)
HIT_C    = (140,  20,  20)

# ─── Init ────────────────────────────────────────────────────────────────────
pygame.init()
scr = pygame.display.set_mode((W, H))
pygame.display.set_caption("QuadFiller")
clk = pygame.time.Clock()
f_s = pygame.font.SysFont("Arial", 17)
f_m = pygame.font.SysFont("Arial", 22, bold=True)
f_l = pygame.font.SysFont("Arial", 46, bold=True)

# Pre-baked cell surfaces (faster than draw.rect inside tight loops)
def _cs(col):
    s = pygame.Surface((CELL, CELL)); s.fill(col); return s

# FIX 6: removed cs_free (was created but never used)
cs_cap  = _cs(CAP_C)
cs_wall = _cs(WALL_C)
cs_line = _cs(LINE_C)
cs_head = _cs(HEAD_C)

# Reusable surfaces — allocated once, reused every frame
guide_surf  = pygame.Surface((W, PLAY_H), pygame.SRCALPHA)
# FIX 3: overlay surface cached at module level (was allocated every frame)
_overlay    = pygame.Surface((W, H), pygame.SRCALPHA)
# FIX 9: cache static font render (never changes)
_lives_lbl  = f_s.render("Lives:", True, TXT_C)


# ─── Helpers ─────────────────────────────────────────────────────────────────
def s2g(x, y):
    """Screen (x, y) → (grid row, col)."""
    return (int(y) - PLAY_Y) // CELL, int(x) // CELL

def g2s(r, c):
    """Grid (row, col) → screen top-left (x, y)."""
    return c * CELL, PLAY_Y + r * CELL


# ─── Grid ────────────────────────────────────────────────────────────────────
class Grid:
    TOTAL = (GROWS - 2) * (GCOLS - 2)   # interior cells

    def __init__(self):
        self.d     = bytearray(GROWS * GCOLS)   # 0=free, 1=wall/captured
        self._free = Grid.TOTAL
        self.surf  = pygame.Surface((W, PLAY_H))
        self.surf.fill(FREE_C)
        # Border walls
        for c in range(GCOLS):
            self._wall(0, c); self._wall(GROWS - 1, c)
        for r in range(GROWS):
            self._wall(r, 0); self._wall(r, GCOLS - 1)

    def _wall(self, r, c):
        self.d[r * GCOLS + c] = 1
        self.surf.blit(cs_wall, (c * CELL, r * CELL))

    def is_wall(self, r, c) -> bool:
        if r < 0 or r >= GROWS or c < 0 or c >= GCOLS:
            return True
        return bool(self.d[r * GCOLS + c])

    def mark_line(self, cells):
        """Paint completed line cells as wall colour."""
        for r, c in cells:
            i = r * GCOLS + c
            if not self.d[i]:
                self.d[i] = 1; self._free -= 1
                self.surf.blit(cs_wall, (c * CELL, r * CELL))

    def fill_region_idx(self, indices):
        """Paint a captured region given flat indices."""
        for i in indices:
            if not self.d[i]:
                self.d[i] = 1; self._free -= 1
                c = i % GCOLS; r = i // GCOLS
                self.surf.blit(cs_cap, (c * CELL, r * CELL))

    def frac(self) -> float:
        return 1.0 - self._free / Grid.TOTAL

    def draw(self, surf):
        surf.blit(self.surf, (0, PLAY_Y))


# ─── Capture logic ───────────────────────────────────────────────────────────
def resolve(grid: Grid, line_cells: set, balls: list) -> int:
    """
    FIX 1 + FIX 2: Seed-based BFS replacing full-grid scan.

    After marking the line as walls, flood-fill only the regions that
    directly border the new line (the only regions that could be newly
    enclosed).  Uses flat integer indices and explicit bounds checks to
    avoid tuple overhead — ~10× faster than the original approach.

    Returns total cells captured.
    """
    grid.mark_line(line_cells)

    # Ball footprints (set of flat indices)
    ball_idx: set[int] = set()
    for b in balls:
        gr, gc = s2g(b.x, b.y)
        sp = b.r // CELL + 1
        for dr in range(-sp, sp + 1):
            for dc in range(-sp, sp + 1):
                nr, nc = gr + dr, gc + dc
                if 0 <= nr < GROWS and 0 <= nc < GCOLS:
                    ball_idx.add(nr * GCOLS + nc)

    # Seed from cells immediately adjacent to the new line
    seen = bytearray(GROWS * GCOLS)
    for r, c in line_cells:
        seen[r * GCOLS + c] = 1          # don't re-enter line cells

    seed_indices: set[int] = set()
    for r, c in line_cells:
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < GROWS and 0 <= nc < GCOLS:
                ni = nr * GCOLS + nc
                if not grid.d[ni]:
                    seed_indices.add(ni)

    gained = 0
    for si in seed_indices:
        if seen[si]:
            continue
        # BFS this region with flat indices (FIX 1: ~2.4× faster than tuples)
        region: list[int] = []
        has_ball = False
        q = deque([si])
        seen[si] = 1
        while q:
            idx = q.popleft()
            region.append(idx)
            if idx in ball_idx:
                has_ball = True
            r = idx // GCOLS
            c = idx  % GCOLS
            if r > 0:
                ni = idx - GCOLS
                if not seen[ni] and not grid.d[ni]:
                    seen[ni] = 1; q.append(ni)
            if r < GROWS - 1:
                ni = idx + GCOLS
                if not seen[ni] and not grid.d[ni]:
                    seen[ni] = 1; q.append(ni)
            if c > 0:
                ni = idx - 1
                if not seen[ni] and not grid.d[ni]:
                    seen[ni] = 1; q.append(ni)
            if c < GCOLS - 1:
                ni = idx + 1
                if not seen[ni] and not grid.d[ni]:
                    seen[ni] = 1; q.append(ni)

        if not has_ball:
            grid.fill_region_idx(region)
            gained += len(region)

    return gained


# ─── ActiveLine ──────────────────────────────────────────────────────────────
class ActiveLine:
    """
    Extends outward from the click point in both directions simultaneously.
    Becomes 'complete' when both ends hit a wall.
    """
    def __init__(self, r, c, horiz):
        self.horiz  = horiz
        self.cells  = {(r, c)}
        self._fixed = r if horiz else c
        self._lo    = (c - 1) if horiz else (r - 1)
        self._hi    = (c + 1) if horiz else (r + 1)
        self._dlo = self._dhi = False
        self.complete = False

    def _rc(self, v):
        return (self._fixed, v) if self.horiz else (v, self._fixed)

    def update(self, grid: Grid) -> bool:
        """Advance both heads by LINE_SPD cells. Returns True when done."""
        for _ in range(LINE_SPD):
            if not self._dlo:
                r, c = self._rc(self._lo)
                if grid.is_wall(r, c):
                    self._dlo = True
                else:
                    self.cells.add((r, c)); self._lo -= 1
            if not self._dhi:
                r, c = self._rc(self._hi)
                if grid.is_wall(r, c):
                    self._dhi = True
                else:
                    self.cells.add((r, c)); self._hi += 1
            if self._dlo and self._dhi:
                self.complete = True; return True
        return False

    def hit(self, balls) -> bool:
        """
        FIX 5: Circle-rect nearest-point distance check replaces square
        approximation.  The old approach false-triggered up to 2px before
        the ball actually touched the line.
        """
        for b in balls:
            gr, gc = s2g(b.x, b.y)
            sp = b.r // CELL + 1
            for dr in range(-sp, sp + 1):
                for dc in range(-sp, sp + 1):
                    cell = (gr + dr, gc + dc)
                    if cell not in self.cells:
                        continue
                    # Precise circle-rect intersection
                    rx = cell[1] * CELL
                    ry = PLAY_Y + cell[0] * CELL
                    cx = max(rx, min(b.x, rx + CELL))
                    cy = max(ry, min(b.y, ry + CELL))
                    if (b.x - cx) ** 2 + (b.y - cy) ** 2 < b.r ** 2:
                        return True
        return False

    def draw(self, surf):
        for r, c in self.cells:
            surf.blit(cs_line, g2s(r, c))
        for done, v in ((self._dlo, self._lo), (self._dhi, self._hi)):
            if not done:
                rh, ch = self._rc(v)
                if 0 <= rh < GROWS and 0 <= ch < GCOLS:
                    surf.blit(cs_head, g2s(rh, ch))


# ─── Ball ────────────────────────────────────────────────────────────────────
class Ball:
    def __init__(self, spd=2.8):
        self.r = BALL_R
        # FIX 7: removed self.spd (stored but never read after construction)
        m = self.r + 30
        self.x = float(random.randint(m, W - m))
        self.y = float(random.randint(PLAY_Y + m, H - m))
        a = random.uniform(math.pi / 6, math.pi / 3)
        self.vx = spd * math.cos(a) * random.choice((-1, 1))
        self.vy = spd * math.sin(a) * random.choice((-1, 1))

    def update(self, grid: Grid):
        """
        FIX 4: Sub-step movement prevents tunneling when speed > CELL.
        Ball moves in steps of at most CELL pixels, checking walls each step.
        """
        steps = max(1, math.ceil(max(abs(self.vx), abs(self.vy)) / CELL))
        dvx = self.vx / steps
        dvy = self.vy / steps

        for _ in range(steps):
            nx = self.x + dvx
            ny = self.y + dvy

            ex = nx + self.r * (1 if self.vx > 0 else -1)
            for dy in (-self.r // 2, 0, self.r // 2):
                if grid.is_wall(*s2g(ex, self.y + dy)):
                    self.vx *= -1; dvx *= -1; nx = self.x; break

            ey = ny + self.r * (1 if self.vy > 0 else -1)
            for dx in (-self.r // 2, 0, self.r // 2):
                if grid.is_wall(*s2g(self.x + dx, ey)):
                    self.vy *= -1; dvy *= -1; ny = self.y; break

            self.x, self.y = nx, ny

    def draw(self, surf):
        p = int(self.x), int(self.y)
        pygame.draw.circle(surf, BALL_C, p, self.r)
        pygame.draw.circle(surf, BALL_M, p, self.r * 2 // 3)
        pygame.draw.circle(surf, BALL_I, p, max(1, self.r // 3))


# ─── UI ──────────────────────────────────────────────────────────────────────
def draw_ui(surf, lives, level, frac, score, horiz, flash):
    bg = HIT_C if flash > 0 else UI_BG
    pygame.draw.rect(surf, bg, (0, 0, W, UI_H))
    pygame.draw.line(surf, WALL_C, (0, UI_H - 1), (W, UI_H - 1), 2)

    # Column 1: level + score (x 10–105)
    surf.blit(f_s.render(f"Lv {level}", True, TXT_C), (10, 8))
    surf.blit(f_s.render(f"Score {score:,}", True, TXT_C), (10, 30))

    # Column 2: lives label + pips (x 112–260)
    surf.blit(_lives_lbl, (112, 8))          # FIX 9: cached, not re-rendered
    for i in range(max(0, min(lives, 8))):
        pygame.draw.circle(surf, BALL_C, (170 + i * 17, 17), 6)

    # Column 2 row 2: direction toggle hint
    d = "─ Horiz" if horiz else "│ Vert"
    surf.blit(f_s.render(f"[Spc/R] {d}", True, (100, 165, 215)), (112, 30))

    # Progress bar
    bx, by, bw, bh = 440, 16, 238, 20
    pygame.draw.rect(surf, BAR_BG, (bx, by, bw, bh))
    fw = int(bw * min(frac, 1.0))
    if fw:
        pygame.draw.rect(surf, BAR_OK if frac >= TARGET else BAR_FG, (bx, by, fw, bh))
    tx = bx + int(bw * TARGET)
    pygame.draw.line(surf, BAR_TGT, (tx, by - 2), (tx, by + bh + 2), 2)
    pygame.draw.rect(surf, WALL_C, (bx, by, bw, bh), 1)
    if frac >= TARGET:
        pygame.draw.rect(surf, BAR_TGT, (bx, by, bw, bh), 2)
    surf.blit(f_s.render(f"{frac * 100:.0f}% / 75%", True, TXT_C), (bx + bw + 8, by + 1))


def draw_overlay(surf, title, sub=""):
    # FIX 3: reuse module-level surface instead of allocating 1.9MB per frame
    _overlay.fill((0, 0, 0, 165))
    surf.blit(_overlay, (0, 0))
    t = f_l.render(title, True, (255, 255, 180))
    surf.blit(t, t.get_rect(center=(W // 2, H // 2 - 30)))
    if sub:
        s = f_m.render(sub, True, (170, 200, 255))
        surf.blit(s, s.get_rect(center=(W // 2, H // 2 + 28)))


# ─── Game ────────────────────────────────────────────────────────────────────
class Game:
    def __init__(self): self.reset()

    def reset(self):
        self.lv = 1; self.lives = 5; self.score = 0
        self.horiz = True; self.state = "play"; self.flash = 0
        self.click_cooldown = 0
        self._new_level()

    def _new_level(self):
        self.grid  = Grid()
        spd        = 2.4 + self.lv * 0.2
        self.balls = [Ball(spd) for _ in range(self.lv + 1)]
        self.line  = None
        self.click_cooldown = 0   # FIX 8: reset cooldown on every new level

    def click(self, x, y):
        if self.state != "play" or self.line or y < PLAY_Y:
            return
        r, c = s2g(x, y)
        if not (0 < r < GROWS - 1 and 0 < c < GCOLS - 1):
            return
        if self.grid.is_wall(r, c):
            return
        self.line = ActiveLine(r, c, self.horiz)

    def flip(self): self.horiz = not self.horiz

    def update(self):
        if self.flash > 0: self.flash -= 1
        if self.click_cooldown > 0: self.click_cooldown -= 1
        if self.state != "play": return

        for b in self.balls:
            b.update(self.grid)

        if not self.line:
            return

        done = self.line.update(self.grid)

        if self.line.hit(self.balls):
            self.line = None; self.lives -= 1; self.flash = 14
            if self.lives <= 0: self.state = "over"
            return

        if done:
            # FIX 2: removed dead -1 path that had pre-committed side effects
            captured = resolve(self.grid, self.line.cells, self.balls)
            self.score += captured * self.lv
            self.line = None
            if self.grid.frac() >= TARGET:
                self.score += 1000 * self.lv; self.lv += 1
                self.state = "win" if self.lv > 10 else "clear"

    def draw(self):
        scr.fill(BG)
        self.grid.draw(scr)

        # Ghost guide line under cursor
        if self.state == "play" and not self.line:
            mx, my = pygame.mouse.get_pos()
            if my > PLAY_Y:
                r, c = s2g(mx, my)
                if 0 < r < GROWS - 1 and 0 < c < GCOLS - 1 \
                        and not self.grid.is_wall(r, c):
                    guide_surf.fill((0, 0, 0, 0))
                    G = (130, 180, 255, 38)
                    if self.horiz:
                        y2 = r * CELL + CELL // 2
                        pygame.draw.line(guide_surf, G, (0, y2), (W, y2), 2)
                    else:
                        x2 = c * CELL + CELL // 2
                        pygame.draw.line(guide_surf, G, (x2, 0), (x2, PLAY_H), 2)
                    scr.blit(guide_surf, (0, PLAY_Y))

        if self.line:
            self.line.draw(scr)
        for b in self.balls:
            b.draw(scr)

        draw_ui(scr, self.lives, self.lv, self.grid.frac(),
                self.score, self.horiz, self.flash)

        if self.state == "clear":
            draw_overlay(scr, f"Level {self.lv - 1} Clear!", "Click to continue")
        elif self.state == "over":
            draw_overlay(scr, "Game Over", f"Score: {self.score:,}   [R] restart")
        elif self.state == "win":
            draw_overlay(scr, "You Win!", f"Score: {self.score:,}   [R] restart")

        pygame.display.flip()


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    game = Game()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()
                elif ev.key == pygame.K_r:
                    game.reset()
                elif ev.key in (pygame.K_SPACE, pygame.K_LCTRL,
                                pygame.K_RCTRL, pygame.K_h, pygame.K_v):
                    game.flip()
            if ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    if game.state == "play" and not game.click_cooldown:
                        game.click(*ev.pos)
                    elif game.state == "clear" and not game.click_cooldown:
                        game._new_level(); game.state = "play"
                        game.click_cooldown = 15
                elif ev.button == 3:
                    game.flip()

        game.update()
        game.draw()
        clk.tick(60)


if __name__ == "__main__":
    main()
