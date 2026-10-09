from nuke_di import Client
from nuke_di.utils import qualname, sname


class Outer:
    class Inner(Client):
        pass


def test_sname_names_a_class_a_function_and_an_instance() -> None:
    def handler() -> None: ...

    assert sname(Outer.Inner) == "Inner"
    assert sname(handler) == "handler"
    # An instance is named after its class
    assert sname(Outer.Inner()) == "Inner"


def test_qualname_keeps_the_outer_class_and_drops_the_function() -> None:
    class Local(Client):
        pass

    assert qualname(Outer.Inner) == "Outer.Inner"
    assert qualname(Local) == "Local"
