import asyncio
import logging
import math
import random
import time

logger = logging.getLogger(__name__)

MOVE_SPEED = 12500  # units/s
TICK_SPEED = 0.05  # seconds


def xcircle(r, a): return round(r * math.cos(a))
def ycircle(r, a): return round(r * math.sin(a))


def norm(vx, vy):
    dist = math.sqrt(vx**2 + vy**2)
    return vx / dist, vy / dist


class BaseAiStrategy:
    def __init__(self, gclient, state, pid):
        self.gclient = gclient
        self.state = state
        self.pid = pid

    def run(self):
        async def wrapper():
            try:
                await self.mainloop()
            except RuntimeError:
                logger.exception('Error in strategy coroutine')
        self.task = asyncio.create_task(wrapper())

    def stop(self):
        self.task.cancel()
        self.task = None

    @property
    def tick_speed(self):
        if self.state.pause_time is not None:
            return 0.2
        else:
            return 0.03

    async def next_tick(self):
        await asyncio.sleep(self.tick_speed)

    @property
    def me(self):
        return self.state.players[self.pid]
    def enemy(self, eid):
        return self.state.enemies[eid]

    def is_ability_casting(self, name):
        self.state.gc_abilities()
        for abi in self.state.abilities.values():
            if abi.name == name and abi.cast_deadline > self.state.time():
                return True
        return False

    def face(self, angle):
        self.me.angle = angle

    def chat(self, msg):
        self.gclient.send('chat', {'u': self.me.name, 'm': msg})

    async def go_to(self, bx, by):
        ax, ay = self.me.x, self.me.y

        if ax == bx and ay == by:
            return

        dx, dy = (bx - ax), (by - ay)
        dx, dy = norm(dx, dy)
        angle = math.atan2(dy, dx)

        t2 = self.state.time()
        while ax != bx or ay != by:
            await self.next_tick()
            t1 = t2
            t2 = self.state.time()

            dt = t2 - t1
            dist = MOVE_SPEED * dt

            rx, ry = (bx - ax), (by - ay)
            rdist = math.sqrt(rx**2 + ry**2)

            if rdist >= dist:
                ax, ay = ax + dx * dist, ay + dy * dist
                moving = True
            else:
                ax, ay = bx, by
                moving = False

            self.state.update_player(self.pid, ax, ay, angle, moving)

    async def until_enemy_spawn(self, name):
        while True:
            for e in self.state.enemies.values():
                if e.name == name:
                    await self.until_delay(random.uniform(100, 300)/1000)
                    return e.id
            await self.next_tick()

    async def until_buff_distributed(self, p, buffs):
        while True:
            for buff in p.buffs:
                if buff in buffs:
                    await self.until_delay(random.uniform(100, 300)/1000)
                    return
            await self.next_tick()

    async def until_buff_gone(self, p, buff):
        while True:
            if buff not in p.buffs:
                await self.until_delay(random.uniform(50, 150)/1000)
                return
            await self.next_tick()

    async def until_delay(self, d):
        deadline = self.state.time() + d
        while self.state.time() < deadline:
            await self.next_tick()

    async def until_ability_starts(self, name):
        self.state.gc_abilities()
        while True:
            for abi in self.state.abilities.values():
                if abi.name == name:
                    return
            await self.next_tick()

    async def until_ability_triggers(self, name):
        self.state.gc_abilities()
        while True:
            for abi in self.state.abilities.values():
                if abi.name == name and abi.cast_deadline < self.state.time():
                    return
            await self.next_tick()


class WyrmholeStrategy(BaseAiStrategy):
    NIDSTINIEN_SIZE = 19500

    def consistent_shuffle(self, pids):
        '''Consistently shuffles some player IDs, but still with some
        pseudo-randomness.'''
        pids.sort()
        seed = sum((p + 3) * (i + 7) for i, p in enumerate(pids))
        rng = random.Random(seed)
        rng.shuffle(pids)

    async def gnash_lash(self, out_first):
        x, y = norm(self.me.x, self.me.y)
        x *= self.NIDSTINIEN_SIZE
        y *= self.NIDSTINIEN_SIZE

        if out_first:
            await self.go_to(x * 1.2, y * 1.2)
            await self.until_ability_triggers('Gnash')
            await self.go_to(x * 0.9, y * 0.9)
            await self.until_ability_triggers('Lash')
        else:
            await self.go_to(x * 0.9, y * 0.9)
            await self.until_ability_triggers('Lash')
            await self.go_to(x * 1.2, y * 1.2)
            await self.until_ability_triggers('Gnash')

    async def mainloop(self):
        GROUP_1 = 9
        GROUP_2 = 10
        GROUP_3 = 11
        JUMP_CIRCLE = 12
        JUMP_FRONT = 13
        JUMP_BACK = 14

        nid = await self.until_enemy_spawn('Nidstinein')
        nid_angle = self.enemy(nid).angle
        nid_front = (
            self.enemy(nid).x + xcircle(self.NIDSTINIEN_SIZE, nid_angle),
            self.enemy(nid).y + ycircle(self.NIDSTINIEN_SIZE, nid_angle),
        )
        nid_back = (
            self.enemy(nid).x + xcircle(self.NIDSTINIEN_SIZE, -nid_angle),
            self.enemy(nid).y + ycircle(self.NIDSTINIEN_SIZE, -nid_angle),
        )
        nid_left = (
            self.enemy(nid).x + xcircle(self.NIDSTINIEN_SIZE, nid_angle+math.pi/2),
            self.enemy(nid).y + ycircle(self.NIDSTINIEN_SIZE, nid_angle+math.pi/2),
        )
        nid_right = (
            self.enemy(nid).x + xcircle(self.NIDSTINIEN_SIZE, nid_angle-math.pi/2),
            self.enemy(nid).y + ycircle(self.NIDSTINIEN_SIZE, nid_angle-math.pi/2),
        )
        face_angle = math.atan2(nid_left[1], nid_left[0])

        spread13_locs = (nid_back, nid_left, nid_right)
        spread2_locs = (
            (nid_left[0]*.95, nid_front[1]),
            (nid_right[0]*.95, nid_front[1]),
        )

        await self.go_to(*nid_front)
        
        await self.until_buff_distributed(self.me, {GROUP_1, GROUP_2, GROUP_3})
        await self.until_delay(0.5)
        if self.me.has_buff(GROUP_1):
            # Prespread in case 1 has no arrows.
            g1_pids = [p.id for p in self.state.players.values()
                            if p.has_buff(GROUP_1)]
            self.consistent_shuffle(g1_pids)
            loc1 = spread13_locs[g1_pids.index(self.me.id)]
            await self.go_to(loc1[0] / 3, loc1[1] / 3)  # Pre-move.
            await self.until_buff_distributed(self.me, {JUMP_CIRCLE, JUMP_FRONT, JUMP_BACK})

            arrows = any(not p.has_buff(JUMP_CIRCLE)
                         for p in self.state.players.values()
                         if p.has_buff(GROUP_1))
            if arrows:
                if self.me.has_buff(JUMP_CIRCLE):
                    loc1 = spread13_locs[0]
                elif self.me.has_buff(JUMP_BACK):
                    loc1 = spread13_locs[1]
                elif self.me.has_buff(JUMP_FRONT):
                    loc1 = spread13_locs[2]

            await self.go_to(loc1[0], loc1[1])
            self.face(face_angle)
            await self.until_delay(2.0)

            out_first = self.is_ability_casting('Gnash and Lash')

            await self.until_buff_gone(self.me, GROUP_1)
            await self.go_to(nid_front[0] * 0.9, nid_front[1] * 0.9)
            await self.gnash_lash(out_first)
            await self.go_to(nid_front[0], nid_front[1])

            await self.until_ability_triggers('Dark High Jump')
            if loc1 == spread13_locs[1]:
                await self.go_to(spread2_locs[0][0], spread2_locs[0][1])
            elif loc1 == spread13_locs[2]:
                await self.go_to(spread2_locs[1][0], spread2_locs[1][1])
            await self.until_ability_triggers('Tower explosion')
            if loc1 != spread13_locs[0]:
                await self.go_to(self.me.x * 1.2, self.me.y)
            await self.until_ability_starts('Geirskogul')
            out_first = self.is_ability_casting('Gnash and Lash')
            await self.go_to(nid_front[0], nid_front[1])

            await self.until_ability_triggers('Dark High Jump')
            if loc1 == spread13_locs[0]:
                await self.go_to(nid_back[0] * 0.9, nid_back[1] * 0.9)
            await self.gnash_lash(out_first)
            await self.until_ability_triggers('Tower explosion')
            if loc1 == spread13_locs[0]:
                await self.go_to(self.me.x, self.me.y * 1.2)
            await self.until_ability_starts('Geirskogul')
            await self.go_to(nid_front[0], nid_front[1])

        elif self.me.has_buff(GROUP_2):
            g2_pids = [p.id for p in self.state.players.values()
                            if p.has_buff(GROUP_2)]
            self.consistent_shuffle(g2_pids)
            await self.until_buff_distributed(self.me, {JUMP_CIRCLE, JUMP_FRONT, JUMP_BACK})
            await self.until_delay(1.5)

            arrows = any(not p.has_buff(JUMP_CIRCLE)
                         for p in self.state.players.values()
                         if p.has_buff(GROUP_2))

            if arrows:
                if self.me.has_buff(JUMP_BACK):
                    choice = 0
                    my_loc = spread2_locs[0]
                elif self.me.has_buff(JUMP_FRONT):
                    choice = 1
                    my_loc = spread2_locs[1]
            else:
                choice = g2_pids.index(self.me.id)
                my_loc = spread2_locs[choice]
                loc_repr = ("back-left", "back-right")[choice]
                self.chat(f"[G2] going {loc_repr}!")

            await self.until_delay(2.0)
            out_first = self.is_ability_casting('Gnash and Lash')
            await self.until_ability_triggers('Dark High Jump')

            await self.gnash_lash(out_first)

            await self.go_to(my_loc[0], my_loc[1])
            self.face(face_angle)
            await self.until_ability_triggers('Dark High Jump')
            await self.go_to(nid_front[0], nid_front[1])
            await self.until_delay(7.0)

            out_first = self.is_ability_casting('Gnash and Lash')
            await self.until_ability_triggers('Dark High Jump')
            my_loc = spread13_locs[choice + 1]
            await self.go_to(my_loc[0] * 0.9, my_loc[1])
            await self.gnash_lash(out_first)
            await self.until_ability_triggers('Tower explosion')
            await self.go_to(my_loc[0] * 1.2, my_loc[1])
            await self.until_ability_starts('Geirskogul')
            await self.go_to(nid_front[0], nid_front[1])

        elif self.me.has_buff(GROUP_3):
            g3_pids = [p.id for p in self.state.players.values()
                            if p.has_buff(GROUP_3)]
            self.consistent_shuffle(g3_pids)
            await self.until_buff_distributed(self.me, {JUMP_CIRCLE, JUMP_FRONT, JUMP_BACK})
            await self.until_delay(0.5)

            arrows = any(not p.has_buff(JUMP_CIRCLE)
                         for p in self.state.players.values()
                         if p.has_buff(GROUP_3))
            if arrows:
                if self.me.has_buff(JUMP_CIRCLE):
                    my_loc = spread13_locs[0]
                elif self.me.has_buff(JUMP_BACK):
                    my_loc = spread13_locs[1]
                elif self.me.has_buff(JUMP_FRONT):
                    my_loc = spread13_locs[2]
            else:
                choice = g3_pids.index(self.me.id)
                my_loc = spread13_locs[choice]
                loc_repr = ("south", "left", "right")[choice]
                self.chat(f"[G3] going {loc_repr}!")

            await self.until_delay(2.0)
            out_first = self.is_ability_casting('Gnash and Lash')
            await self.until_ability_triggers('Dark High Jump')
            await self.go_to(my_loc[0] * 0.9, my_loc[1] * 0.9)
            await self.gnash_lash(out_first)
            await self.until_ability_triggers('Tower explosion')
            await self.go_to(my_loc[0] * 1.2, my_loc[1] * 1.2)
            await self.until_ability_starts('Geirskogul')
            await self.go_to(my_loc[0] * 0.8, my_loc[1] * 0.8)
            await self.until_ability_triggers('Geirskogul')
            await self.go_to(my_loc[0], my_loc[1])
            self.face(face_angle)

            await self.until_delay(5.0)
            out_first = self.is_ability_casting('Gnash and Lash')
            await self.until_ability_triggers('Dark High Jump')
            await self.go_to(nid_front[0] * 0.9, nid_front[1] * 0.9)
            await self.gnash_lash(out_first)
            await self.go_to(nid_front[0], nid_front[1])


def find_strategy(tts):
    if tts == 'Starting Wyrmhole':
        return WyrmholeStrategy
