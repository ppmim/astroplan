# Licensed under a 3-clause BSD style license - see LICENSE.rst
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import numpy as np
from astropy.time import Time
import astropy.units as u
from astropy.coordinates import SkyCoord

from ..utils import time_grid_from_range
from ..observer import Observer
from ..target import FixedTarget
from ..constraints import (AirmassConstraint, AtNightConstraint, _get_altaz,
                           MoonIlluminationConstraint)
from ..scheduling import (ObservingBlock, PriorityScheduler, SequentialScheduler,
                          Transitioner, TransitionBlock, Schedule, Slot, Scorer)

vega = FixedTarget(coord=SkyCoord(ra=279.23473479 * u.deg, dec=38.78368896 * u.deg),
                   name="Vega")
rigel = FixedTarget(coord=SkyCoord(ra=78.63446707 * u.deg, dec=8.20163837 * u.deg),
                    name="Rigel")
polaris = FixedTarget(coord=SkyCoord(ra=37.95456067 * u.deg,
                                     dec=89.26410897 * u.deg), name="Polaris")

apo = Observer.at_site('apo')
targets = [vega, polaris, rigel]
default_time = Time('2016-02-06 03:00:00')
only_at_night = [AtNightConstraint()]


def test_observing_block():
    block = ObservingBlock(rigel, 1*u.minute, 0, configuration={'filter': 'b'})
    assert(block.configuration['filter'] == 'b')
    assert(block.target == rigel)
    times_per_exposure = [1*u.minute, 4*u.minute, 15*u.minute, 5*u.minute]
    numbers_of_exposures = [100, 4, 3, 12]
    readout_time = 0.5*u.minute
    for index in range(len(times_per_exposure)):
        block = ObservingBlock.from_exposures(vega, 0, times_per_exposure[index],
                                              numbers_of_exposures[index], readout_time)
        assert(block.duration == numbers_of_exposures[index] *
               (times_per_exposure[index] + readout_time))


def test_slot():
    start_time = default_time
    end_time = start_time + 24 * u.hour
    slot = Slot(start_time, end_time)
    slots = slot.split_slot(start_time, start_time+1*u.hour)
    assert len(slots) == 2
    assert slots[0].end == slots[1].start


def test_schedule():
    start_time = default_time
    end_time = start_time + 24 * u.hour
    schedule = Schedule(start_time, end_time)
    assert schedule.slots[0].start == start_time
    assert schedule.slots[0].end == end_time
    assert schedule.slots[0].duration == 24*u.hour
    schedule.new_slots(0, start_time, end_time)
    assert len(schedule.slots) == 1
    new_slots = schedule.new_slots(0, start_time+1*u.hour, start_time+4*u.hour)
    assert np.abs(new_slots[0].duration - 1*u.hour) < 1*u.second
    assert np.abs(new_slots[1].duration - 3*u.hour) < 1*u.second
    assert np.abs(new_slots[2].duration - 20*u.hour) < 1*u.second


def test_schedule_insert_slot():
    schedule = Schedule(default_time, default_time + 5*u.hour)
    # testing for when float comparison doesn't work, does it start/end at the right time
    duration = 2*u.hour + 1*u.second
    end_time = default_time + duration
    block = TransitionBlock.from_duration(duration)
    schedule.insert_slot(end_time - duration, block)
    assert not end_time - duration == default_time
    assert len(schedule.slots) == 2
    assert schedule.slots[0].start == default_time
    schedule = Schedule(default_time, default_time + 5*u.hour)
    # testing for when float evaluation does work
    duration = 2*u.hour
    end_time = default_time + duration
    block = TransitionBlock.from_duration(duration)
    schedule.insert_slot(end_time - duration, block)
    assert end_time - duration == default_time
    assert len(schedule.slots) == 2
    assert schedule.slots[0].start == default_time


def test_schedule_change_slot_block():
    schedule = Schedule(default_time, default_time + 5 * u.hour)
    duration = 2 * u.hour
    block = TransitionBlock.from_duration(duration)
    schedule.insert_slot(default_time, block)
    # check that it has the correct duration
    assert np.abs(schedule.slots[0].end - duration - default_time) < 1*u.second
    new_duration = 1*u.minute
    new_block = TransitionBlock.from_duration(new_duration)
    schedule.change_slot_block(0, new_block)
    # check the duration changed properly, and slots are still consecutive/don't overlap
    assert np.abs(schedule.slots[0].end - new_duration - default_time) < 1*u.second
    assert schedule.slots[1].start == schedule.slots[0].end


def test_transitioner():
    blocks = [ObservingBlock(t, 55 * u.minute, i) for i, t in enumerate(targets)]
    slew_rate = 1 * u.deg / u.second
    trans = Transitioner(slew_rate=slew_rate)
    start_time = default_time
    transition = trans(blocks[0], blocks[2], start_time, apo)
    aaz = _get_altaz(Time([start_time]), apo,
                     [blocks[0].target, blocks[2].target])['altaz']
    sep = aaz[0].separation(aaz[1])[0]
    assert isinstance(transition, TransitionBlock)
    assert transition.duration == sep/slew_rate
    blocks = [ObservingBlock(vega, 10*u.minute, 0, configuration={'filter': 'v'}),
              ObservingBlock(vega, 10*u.minute, 0, configuration={'filter': 'i'}),
              ObservingBlock(rigel, 10*u.minute, 0, configuration={'filter': 'i'})]
    trans = Transitioner(slew_rate, instrument_reconfig_times={'filter': {('v', 'i'): 2*u.minute,
                                                                          'default': 5*u.minute}})
    transition1 = trans(blocks[0], blocks[1], start_time, apo)
    transition2 = trans(blocks[0], blocks[2], start_time, apo)
    transition3 = trans(blocks[1], blocks[0], start_time, apo)
    assert np.abs(transition1.duration - 2*u.minute) < 1*u.second
    assert np.abs(transition2.duration - 2*u.minute - transition.duration) < 1*u.second
    # to test the default transition
    assert np.abs(transition3.duration - 5*u.minute) < 1*u.second
    assert transition1.components is not None

default_transitioner = Transitioner(slew_rate=1 * u.deg / u.second)


def test_priority_scheduler():
    constraints = [AirmassConstraint(3, boolean_constraint=False)]
    blocks = [ObservingBlock(t, 55*u.minute, i) for i, t in enumerate(targets)]
    start_time = default_time
    end_time = start_time + 18*u.hour
    scheduler = PriorityScheduler(transitioner=default_transitioner,
                                  constraints=constraints, observer=apo,
                                  time_resolution=2*u.minute)
    schedule = Schedule(start_time, end_time)
    scheduler(blocks, schedule)
    assert len(schedule.observing_blocks) == 3
    assert all(np.abs(block.end_time - block.start_time - block.duration) <
               1*u.second for block in schedule.scheduled_blocks)
    assert all([schedule.observing_blocks[0].target == polaris,
                schedule.observing_blocks[1].target == rigel,
                schedule.observing_blocks[2].target == vega])
    # polaris and rigel both peak just before the start time
    assert schedule.slots[0].block.target == polaris
    assert schedule.slots[2].block.target == rigel


def test_sequential_scheduler():
    constraints = [AirmassConstraint(2.5, boolean_constraint=False)]
    blocks = [ObservingBlock(t, 55 * u.minute, i) for i, t in enumerate(targets)]
    start_time = default_time
    end_time = start_time + 18 * u.hour
    scheduler = SequentialScheduler(constraints=constraints, observer=apo,
                                    transitioner=default_transitioner)
    schedule = Schedule(start_time, end_time)
    scheduler(blocks, schedule)
    assert len(schedule.observing_blocks) > 0
    assert all(np.abs(block.end_time - block.start_time - block.duration) <
               1*u.second for block in schedule.scheduled_blocks)
    assert all([schedule.observing_blocks[0].target == rigel,
                schedule.observing_blocks[1].target == polaris,
                schedule.observing_blocks[2].target == vega])
    # vega rises late, so its start should be later
    assert schedule.observing_blocks[2].start_time > start_time + 8*u.hour


def test_scheduling_target_down():
    lco = Observer.at_site('lco')
    block = [ObservingBlock(FixedTarget.from_name('polaris'), 1 * u.min, 0)]
    start_time = default_time
    end_time = start_time + 3*u.day
    scheduler1 = SequentialScheduler(start_time, end_time, only_at_night, lco,
                                     default_transitioner, gap_time=2*u.hour)
    schedule1 = scheduler1(block)
    assert len(schedule1.observing_blocks) == 0
    scheduler2 = PriorityScheduler(start_time, end_time, only_at_night, lco,
                                   default_transitioner, time_resolution=30 * u.minute)
    schedule2 = scheduler2(block)
    assert len(schedule2.observing_blocks) == 0


def test_scheduling_during_day():
    block = [ObservingBlock(FixedTarget.from_name('polaris'), 1 * u.min, 0)]
    day = default_time
    start_time = apo.midnight(day) + 10*u.hour
    end_time = start_time + 6*u.hour
    scheduler1 = SequentialScheduler(start_time, end_time, only_at_night, apo,
                                     default_transitioner, gap_time=30*u.minute)
    schedule1 = scheduler1(block)
    assert len(schedule1.observing_blocks) == 0
    scheduler2 = PriorityScheduler(start_time, end_time, only_at_night, apo,
                                   default_transitioner, time_resolution=2 * u.minute)
    schedule2 = scheduler2(block)
    assert len(schedule2.observing_blocks) == 0
# bring this back when MoonIlluminationConstraint is working properly


def test_scheduling_moon_up():
    block = [ObservingBlock(FixedTarget.from_name('polaris'), 30 * u.min, 0)]
    # on february 23 the moon was up between the start/end times defined below
    day = default_time + 17 * u.day
    start_time = apo.midnight(day) - 2 * u.hour
    end_time = start_time + 6 * u.hour
    constraints = [AtNightConstraint(), MoonIlluminationConstraint(max=0)]
    scheduler1 = SequentialScheduler(start_time, end_time, constraints, apo,
                                     default_transitioner, gap_time=30*u.minute)
    schedule1 = scheduler1(block)
    assert len(schedule1.observing_blocks) == 0
    scheduler2 = PriorityScheduler(start_time, end_time, constraints, apo,
                                   default_transitioner, time_resolution=20*u.minute)
    schedule2 = scheduler2(block)
    assert len(schedule2.observing_blocks) == 0

def test_scorer():
    constraint = AirmassConstraint(max=4)
    times = time_grid_from_range(Time(['2016-02-06 00:00', '2016-02-06 08:00']),
                                 time_resolution=20*u.minute)
    c = constraint(apo, [vega, rigel], times)
    block = ObservingBlock(vega, 1*u.hour, 0, constraints=[constraint])
    block2 = ObservingBlock(rigel, 1*u.hour, 0, constraints=[constraint])
    scorer = Scorer.from_start_end([block, block2], apo, Time('2016-02-06 00:00'),
                                   Time('2016-02-06 08:00'))
    scores = scorer.create_score_array(time_resolution=20*u.minute)
    assert np.array_equal(c, scores)

    constraint2 = AirmassConstraint(max=2, boolean_constraint=False)
    c2 = constraint2(apo, [vega, rigel], times)
    block = ObservingBlock(vega, 1*u.hour, 0, constraints=[constraint])
    block2 = ObservingBlock(rigel, 1*u.hour, 0, constraints=[constraint2])
    # vega's score should be = c[0], rigel's should be =  c2[1]
    scorer = Scorer.from_start_end([block, block2], apo, Time('2016-02-06 00:00'),
                                   Time('2016-02-06 08:00'))
    scores = scorer.create_score_array(time_resolution=20 * u.minute)
    assert np.array_equal(c[0], scores[0])
    assert np.array_equal(c2[1], scores[1])

    block = ObservingBlock(vega, 1*u.hour, 0)
    block2 = ObservingBlock(rigel, 1*u.hour, 0)
    scorer = Scorer.from_start_end([block, block2], apo, Time('2016-02-06 00:00'),
                                   Time('2016-02-06 08:00'), [constraint2])
    scores = scorer.create_score_array(time_resolution=20 * u.minute)
    # the ``global_constraint``: constraint2 should have applied to the blocks
    assert np.array_equal(c2, scores)
