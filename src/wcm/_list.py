import logging
import json
import wings
import os
import click
from wcm import _schema, _utils
from contextlib import contextmanager

logger = logging.getLogger()


@contextmanager
def _cli(**kw):
    i = None
    try:
        logger.debug("Initializing WINGS API Client")
        i = wings.init(**kw)
        yield i
    finally:
        if i:
            i.close()


def list_components(profile="default"):
    outp = ""
    with _cli(profile=profile) as wings_instance:
        component = wings_instance.component.get_all_items()
        component = component["children"]
        for i in component:
            comp_class = ((i["cls"])["component"])["id"]
            comp_class = comp_class.split('#')

            outp += "Class: " + comp_class[-1] + "\n"

            length = len(i["children"])
            count = 1
            outp += "   └────┐\n"
            for j in i["children"]:
                comp_id = ((j["cls"])["component"])["id"]
                comp_id = comp_id.split('#')

                if length == 1 or count == length:
                    outp += "\t└─ " + comp_id[-1] + "\n"
                else:
                    outp += "\t├─ " + comp_id[-1] + "\n"

                count += 1

        click.echo(outp)


def _main():
    list_components()


if __name__ == "__main__":
    try:
        _main()
    except Exception as e:
        logger.exception(e)
