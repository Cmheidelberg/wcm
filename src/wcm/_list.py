import logging
import json
import wings
import os
import click
from wcm import _schema, _utils
from contextlib import contextmanager
import requests

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


def list_components(profile="default", wings=False):
    outp = ""

    if wings:
        with _cli(profile=profile) as wings_instance:
            component = wings_instance.component.get_all_items()
            component = component["children"]
            for i in component:
                try:
                    comp_class = ((i["cls"])["component"])["id"]
                    comp_class = comp_class.split('#')

                    outp += "[" + comp_class[-1] + "]\n"

                    length = len(i["children"])
                    count = 1

                    if length > 0:
                        outp += "  └─┐\n"

                    for j in i["children"]:
                        comp_id = ((j["cls"])["component"])["id"]
                        comp_id = comp_id.split('#')

                        if length == 1 or count == length:
                            outp += "    └─ " + comp_id[-1] + "\n"
                        else:
                            outp += "    ├─ " + comp_id[-1] + "\n"
                        count += 1

                    outp += "\n"
                except:
                    logger.error("Wings error: Maybe, the component is corrupted.")

    else:
        repo = "mintproject/wcm-components"
        git_session = requests.Session()

        # get the gitHub api credentials from the wcm credentials file
        creds = _utils.github_credentials(profile)

        if len(creds) > 0:
            # gives session credentials
            username = creds[0]  # for authentication and committing
            token = creds[1]
            git_session.auth = (username, token)
        else:
            logger.warning("Could not authenticate gitHub credentials from wcm credentials file")
            logger.info("Listing repo without credentials")

        try:
            # gets the tree sha of most recent version of master branch
            master = git_session.get("https://api.github.com/repos/%s/branches/master" % repo)
            master = json.loads(master.text)
            sha = master['commit']['sha']

            r = git_session.get("https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, sha))
            jout = json.loads(r.text)
            tree = jout["tree"]
        except KeyError:
            tree = {}
            logger.error("Something went wrong accessing gitHub. Maybe the github token is incorrect?")

        # Prints out tree sha
        for i in tree:
            length = len((i["path"]).split("/"))
            if length == 1:
                outp += "\n[" + i["path"] + "]\n"
            elif length == 2:
                version = (i["path"]).split("/")
                version = version[1]
                version = version.split(".")[0]
                outp += "    ├─ " + i["path"].split("/")[0] + "-" + version + "\n"

    # print out the list
    click.echo(outp)

def _main():
    list_components()


if __name__ == "__main__":
    try:
        _main()
    except Exception as e:
        logger.exception(e)
