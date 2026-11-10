"""Map data projection and position along a looping route (IVI navigation)."""
import json
import math

import pytest

from bench.map_data import build_map, project
from dashboard.ivi.nav import Route, position


def test_projection_is_local_metres_with_north_positive():
    assert project(126.9779, 37.5663, 126.9779, 37.5663) == (0.0, 0.0)
    x, y = project(126.9779, 37.5663 + 0.001, 126.9779, 37.5663)
    assert x == 0.0 and y == pytest.approx(110.6, abs=1.0)           # 0.001 deg latitude ~ 110.6 m
    x, _ = project(126.9779 + 0.001, 37.5663, 126.9779, 37.5663)
    assert x == pytest.approx(88.2, abs=1.5)                          # 0.001 deg longitude at 37.57N ~ 88 m


def test_build_map_projects_roads_and_route_and_drops_degenerate_ways():
    route = {'coordinates': [[126.9779, 37.5663], [126.9789, 37.5663], [126.9789, 37.5673], [126.9779, 37.5663]]}
    roads = {'ways': [{'class': 'primary', 'name': 'a', 'geometry': [[126.9779, 37.5663], [126.9780, 37.5664]]},
                      {'class': 'residential', 'name': 'point', 'geometry': [[126.9779, 37.5663]]}],
             'attribution': 'x'}
    data = build_map(route, roads, (126.9779, 37.5663))
    assert len(data['roads']) == 1 and data['roads'][0]['c'] == 'primary' and len(data['roads'][0]['p']) == 4
    assert data['route']['length'] == pytest.approx(sum(math.dist(a, b) for a, b in zip(
        zip(data['route']['p'][0::2], data['route']['p'][1::2]), zip(data['route']['p'][2::2], data['route']['p'][3::2]))), rel=1e-6)
    assert data['attribution'] == 'x' and json.dumps(data)


def square():
    return Route.from_flat([0, 0, 100, 0, 100, 100, 0, 100, 0, 0])      # 400 m loop, counter-clockwise from the origin


def test_position_interpolates_headings_and_wraps_around_the_loop():
    route = square()
    assert route.length == pytest.approx(400)
    x, y, heading = position(route, 50)
    assert (round(x), round(y)) == (50, 0) and heading == pytest.approx(90)         # heading east
    x, y, heading = position(route, 150)
    assert (round(x), round(y)) == (100, 50) and heading == pytest.approx(0)        # heading north
    assert position(route, 450)[:2] == pytest.approx(position(route, 50)[:2])       # wraps
    assert position(route, -50)[:2] == pytest.approx(position(route, 350)[:2])


def test_route_needs_two_points():
    with pytest.raises(ValueError):
        Route.from_flat([0, 0])
