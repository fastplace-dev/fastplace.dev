"""Database isolation modes (supp-1-G4) and model factories (sweep-G13).

``transactional_db`` wraps each test in a transaction that is rolled back;
``clean_db`` gives a fresh schema wiped of data; ``ModelFactory`` builds
model instances with sensible defaults. All through pytester subprocesses —
they swap the process-wide database manager.
"""

from __future__ import annotations


def test_transactional_db_rolls_back_between_tests(pytester, mini_app, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_txn="""
        from app.models import Widget


        async def test_write_is_visible_inside(transactional_db):
            await Widget.create(name="inside")
            assert await Widget.count() == 1


        async def test_previous_write_was_rolled_back(transactional_db):
            assert await Widget.count() == 0
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=2)


def test_fastplace_models_marker_imports_nonstandard_model_modules(
    pytester, mini_app, set_fastplace_ini
):
    """Models outside ``app/models`` join the schema via the marker.

    Scaffolded apps keep models under ``app/modules/<name>/models/`` — the
    plugin cannot know those module paths, so isolation fixtures accept a
    ``fastplace_models`` marker listing the modules to import before the
    schema is built.
    """
    set_fastplace_ini(None)
    entities = pytester.path / "app" / "entities"
    entities.mkdir()
    (entities / "__init__.py").write_text("")
    (entities / "gadget.py").write_text(
        '''"""
A model in a nonstandard module — imported only via the marker.
"""

from fastplace.orm import Field, Model


class Gadget(Model):
    __tablename__ = "gadgets_marked"

    __fillable__ = {"label"}

    id: int = Field(primary_key=True)
    label: str = Field(default="")
'''
    )
    pytester.makepyfile(
        test_marked="""
        import pytest


        @pytest.mark.fastplace_models("app.entities.gadget")
        async def test_marked_module_tables_exist(transactional_db):
            from app.entities.gadget import Gadget

            await Gadget.create(label="on")
            assert await Gadget.count() == 1
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


def test_clean_db_wipes_data_between_tests(pytester, mini_app, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_clean="""
        from app.models import Widget


        async def test_first_test_leaves_a_row(clean_db):
            await Widget.create(name="a")
            assert await Widget.count() == 1


        async def test_second_test_starts_empty(clean_db):
            assert await Widget.count() == 0
            await Widget.create(name="b")


        async def test_third_still_empty(clean_db):
            assert await Widget.count() == 0
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=3)


def test_model_factory_makes_creates_and_sequences(pytester, mini_app, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_factory="""
        from app.models import Widget
        from fastplace.testing import ModelFactory


        class WidgetFactory(ModelFactory):
            model = Widget

            async def definition(self):
                return {"name": f"widget-{self.sequence('w')}"}


        async def test_make_returns_an_unsaved_instance(clean_db):
            factory = WidgetFactory()
            unsaved = await factory.make(name="manual")
            assert unsaved.name == "manual"
            assert unsaved.id is None
            assert await Widget.count() == 0


        async def test_create_persists_with_overrides(clean_db):
            factory = WidgetFactory()
            a = await factory.create()
            b = await factory.create(name="custom")
            assert a.id is not None and b.id is not None
            assert b.name == "custom"
            assert a.name != b.name  # sequence advanced
            assert await Widget.count() == 2


        async def test_create_many_builds_a_batch(clean_db):
            factory = WidgetFactory()
            batch = await factory.create_many(3)
            assert len(batch) == 3
            assert len({w.name for w in batch}) == 3  # unique sequences
            assert await Widget.count() == 3


        async def test_sequences_advance_across_instances(clean_db):
            first = await WidgetFactory().create()
            second = await WidgetFactory().create()
            assert first.name != second.name
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=4)


def test_model_factory_requires_a_model(pytester, set_fastplace_ini):
    set_fastplace_ini(None)
    pytester.makepyfile(
        test_broken="""
        import pytest

        from fastplace.testing import ModelFactory


        class BrokenFactory(ModelFactory):
            model = None


        def test_instantiation_refuses():
            with pytest.raises(TypeError):
                BrokenFactory()
        """
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)
