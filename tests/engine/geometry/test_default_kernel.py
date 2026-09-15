"""Which kernel the engine uses when nobody passes one."""

import sys
from collections.abc import Iterator

import pytest

from caliper.contracts.commands import CreateCircle
from caliper.contracts.document import EntityId, Point2
from caliper.contracts.errors import Error, ErrorCode
from caliper.contracts.kernel import Kernel
from caliper.contracts.queries import AreaProperties
from caliper.engine.commands.bus import Bus
from caliper.engine.geometry import default_kernel
from caliper.engine.geometry.fake_kernel import FakeKernel

E1 = EntityId("e1")
CIRCLE = CreateCircle(center=Point2(x=0.0, y=0.0), radius=1.0)


@pytest.fixture(autouse=True)
def fresh_default() -> Iterator[None]:
    default_kernel.cache_clear()
    yield
    default_kernel.cache_clear()


def test_the_default_is_occt_when_the_extra_is_installed() -> None:
    occt = pytest.importorskip(
        "caliper.engine.geometry.occt_kernel", reason="the occt extra isn't installed"
    )
    kernel = default_kernel()
    assert isinstance(kernel, occt.OCCTKernel)
    assert default_kernel() is kernel


def test_without_the_extra_there_is_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "caliper.engine.geometry.occt_kernel", None)
    assert default_kernel() is None
    bus = Bus()
    bus.execute(CIRCLE)
    result = bus.queries.area_properties([E1])
    assert isinstance(result, Error)
    assert result.code == ErrorCode.KERNEL_UNAVAILABLE
    assert "occt extra" in result.message


def test_the_kernel_is_only_looked_up_for_queries_that_need_one() -> None:
    lookups: list[None] = []

    def counting() -> Kernel | None:
        lookups.append(None)
        return FakeKernel()

    bus = Bus(kernel=counting)
    bus.execute(CIRCLE)
    assert bus.queries.entity_at_point(Point2(x=1.0, y=0.0), 0.1) == E1
    assert bus.queries.bounding_box() is not None
    assert lookups == []
    assert isinstance(bus.queries.area_properties([E1]), AreaProperties)
    assert len(lookups) == 1


def test_an_explicit_none_means_no_kernel() -> None:
    bus = Bus(kernel=None)
    bus.execute(CIRCLE)
    result = bus.queries.area_properties([E1])
    assert isinstance(result, Error)
    assert result.code == ErrorCode.KERNEL_UNAVAILABLE
