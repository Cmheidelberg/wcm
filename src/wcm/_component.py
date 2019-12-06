#!/usr/bin/env python3
"""Component Uploader."""

import argparse
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from shutil import make_archive
import requests
import json
import base64
import wings
import yaml
from semver import parse_version_info
from yaml import load
import click

from wcm import _schema, _utils

try:
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader

log = logging.getLogger()


@contextmanager
def _cli(**kw):
    i = None
    try:
        log.debug("Initializing WINGS API Client")
        i = wings.init(**kw)
        yield i
    finally:
        if i:
            i.close()


def check_data_types(spec):
    _types = set()
    for _t in spec["inputs"]:
        if not _t["isParam"] and _t["type"] not in _types:
            dtype = _t["type"][6:]
            if dtype not in spec.get("data", {}):
                log.warning(f"input data-type \"{dtype}\" not defined")

    for _t in spec["outputs"]:
        if not _t["isParam"] and _t["type"] not in _types:
            if dtype not in spec.get("data", {}):
                log.warning(f"output data-type \"{dtype}\" not defined")


def create_data_types(spec, component_dir, cli, ignore_data):
    for dtype, _file in spec.get("data", {}).items():
        cli.data.new_data_type(dtype, None)
        if ignore_data:
            continue

        if _file:
            # Properties
            format = _file.get("format", None)
            metadata_properties = _file.get("metadataProperties", {})
            if metadata_properties or format:
                cli.data.add_type_properties(
                    dtype, properties=metadata_properties, format=format
                )

            # Files
            for f in _file.get("files", ()):
                cli.data.upload_data_for_type(
                    (component_dir / Path(f)).resolve(), dtype
                )


def component_exists(spec, profile, overwrite, credentials):
    """
    :param spec: Component specification
    :type spec: dict
    :param profile: If we are using the cli, User profile with credentials required
    :type profile: dict
    :param overwrite: Overwrite component
    :type overwrite: bool
    :param credentials: If we are using the API, Credentials required
    :type credentials: dict
    :return: Boolean
    :rtype: bool
    """
    with _cli(profile=profile, **credentials) as wi:
        if spec["version"].isspace() or len(spec["version"]) <= 0:
            name = spec["name"]
        else:
            name = spec["name"] + "-" + spec["version"]

        comps = wi.component.get_component_description(name)
        if comps is not None:
            log.info("Component already exists on server")
            if not overwrite:
                log.error("Publishing this component would overwrite the existing one. To force upload use flag -f")
            return True
        return False


def github_deploy(component_dir, overwrite, profile):
    repo = "cmheidelberg/wcm-components"
    session = requests.Session()

    name = ""
    version = ""
    replace_sha = ""
    tree = {}
    file_yaml = None

    # get the gitHub api credentials from the wcm credentials file
    creds = _utils.github_credentials(profile)

    if len(creds) > 0:
        # gives session credentials
        username = creds[0]  # for authentication and committing
        token = creds[1]
        session.auth = (username, token)
    else:
        log.error("Could not authenticate GitHub credentials from wcm credentials file")
        log.info("Cannot publish component without proper credentials")
        exit(1)

    try:
        # gets the tree sha of most recent version of master branch
        master = session.get("https://api.github.com/repos/%s/branches/master" % repo)
        master = json.loads(master.text)
        sha = master['commit']['sha']

        r = session.get("https://api.github.com/repos/%s/git/trees/%s?recursive=1" % (repo, sha))
        jout = json.loads(r.text)
        tree = jout["tree"]
    except KeyError:
        log.error("Something went wrong accessing GitHub. Maybe the GitHub username or token is incorrect?")
        exit(1)

    # Once authentication is set up start deploying to GitHub
    log.info("Uploading component to GitHub")

    if not os.path.exists(component_dir):
        log.error("Could not find \"%s\" in path" % os.path.basename(component_dir))
        exit(1)

    # Reads the yaml and gets componentType and version
    try:
        try:
            stream = open(os.path.join(component_dir, "wings-component.yaml"), 'r')
        except FileNotFoundError:
            stream = open(os.path.join(component_dir, "wings-component.yml"), 'r')

        file_yaml = (yaml.safe_load(stream))

    except FileNotFoundError:
        log.error("could not find \"wings-component.yaml\" within %s aborting upload" % component_dir)
        exit(1)

    # Gets the model's name and version from yaml file
    try:
        name = file_yaml['name'].lower()
        version = file_yaml["version"].lower()
    except KeyError:
        log.error("Could not ascertain component or version from YAML. Check to make sure YAML is configured properly")
        exit(1)

    # Checks to see if the component already exists in GitHub
    # if component is already in repo set record to component's sha so it can be properly overridden
    # errors if wcm overwrite flag not set to True
    for i in tree:
        if i['path'] == name:
            url = i['url']
            url_folder = session.get(url)
            url_folder = json.loads(url_folder.text)
            for j in url_folder['tree']:
                if j['path'].split(".")[0] == version:
                    log.info("Component already exists in repo")
                    if overwrite:
                        replace_sha = j['sha']
                        log.info("Overwriting component")
                    else:
                        log.error("Publishing this component would overwrite the existing one. "
                                  "To force upload use flag -f")
                        exit(1)

    # Zips up and base64 encript the folderfor uploading
    _c = make_archive("tmp", "zip", component_dir)

    with open("tmp.zip", "rb") as f:
        zip_bytes = f.read()
        encoded = base64.b64encode(zip_bytes).decode('utf-8')

    # message should be from yaml information
    params = {"message": "(wcm) Uploaded " + name,
              "committer": {
                  "name": "WCM",
                  "email": "NONE"
              },
              "content": str(encoded)
              }

    if len(replace_sha) > 0:
        params['sha'] = replace_sha

    git_path = name + "/" + version + ".zip"
    url = 'https://api.github.com/repos/%s/wcm-components/contents/%s' % (username, git_path)

    # Attempts to add the code to the repo
    p = session.put(url, json.dumps(params))

    if p.status_code == 422:
        log.error("Upload Failed. Possibly because the component already exists in GitHub")
        exit(1)
    elif p.status_code == 400:
        log.info("Upload Successful")
    elif p.status_code == 404:
        log.info("could not find repository")
        exit(1)

    os.remove(_c)

    log.info("Making pull request")

    base_url = 'https://api.github.com/repos/mintproject/wcm-components/pulls'
    pr = {'title': '(wcm) New component(s) added',
          'body': 'This pr was automatically made by wcm',
          "committer": {
              "name": "WCM",
              "email": "none"
          },
          'head': '%s:master' % username,
          'base': 'master'}

    p = session.post(base_url, json.dumps(pr))

    if p.status_code == 422:
        log.info("Current pull request still active. Updating instead")
        most_recent_commit = session.get("https://api.github.com/repos/mintproject/wcm-components/pulls")
        most_recent_commit = json.loads(most_recent_commit.text)

        commit_number = (most_recent_commit[0])["number"]
        headers = {'Accept': 'application/vnd.github.lydian-preview+json'}

        p = session.put(base_url + "/" + str(commit_number) + "/update-branch", headers=headers)

    if p.status_code == 202:
        log.info("Successfully updated pull with new component")
    elif p.status_code == 201:
        log.info("Successfully made pull request with new component")
    elif p.status_code == 404:
        log.error("Could not find either head or base repository from url")
        exit(1)
    else:
        log.error("Unexpected status code from github api: %s" % p.status_code)
        exit(1)


def wings_deploy(component_dir, overwrite, creds, profile, debug, ignore_data, dry_run):

    with _cli(profile=profile, **creds) as cli:
        try:
            spec = load((component_dir / "wings-component.yml").open(), Loader=Loader)
        except FileNotFoundError:
            spec = load((component_dir / "wings-component.yaml").open(), Loader=Loader)

        try:
            _schema.check_package_spec(spec)
        except ValueError as err:
            log.error(err)
            exit(1)

        name = spec["name"]
        version = spec["version"]

        # _id = f"{name}-v{version}" #removed this line because it would make errors if 'v' was in version name
        if version.isspace() or len(version) <= 0:
            log.warning("No version. Component will be uploaded with no version identifier")
            _id = name
        else:
            _id = name + "-" + version

        if component_exists(spec, profile, overwrite, creds):
            if overwrite:
                log.info("Replacing the component")
            else:
                log.info("Skipping publish")
                return cli.component.get_component_description(_id)
        else:
            log.info("Component does not exist, deploying the component")

        if ignore_data:
            log.info("Upload data and metadata skipped")

        wings_component = spec["wings"]
        log.debug("Check component's data-types")
        check_data_types(wings_component)
        log.debug("Create component's data-types")
        create_data_types(wings_component, component_dir, cli, ignore_data)

        log.debug("Create component's type")
        cli.component.new_component_type(wings_component["componentType"], None)

        log.debug("Create the component")
        cli.component.new_component(_id, wings_component["componentType"])

        log.debug("Create component's I/O, Documentation, etc.")
        cli.component.save_component(_id, wings_component)

        try:
            _c = make_archive("_c", "zip", component_dir / "src")
            log.debug("Upload component code")
            cli.component.upload_component(_c, _id)
            return cli.component.get_component_description(_id)
        finally:
            os.remove(_c)


def deploy_component(component_dir, profile="default", debug=False, dry_run=False,
                     ignore_data=False, overwrite=None, wings=False):
    component_dir = Path(component_dir)

    if not wings:
        github_deploy(component_dir, overwrite, profile)

    # runs if wings true
    else:
        wings_deploy(component_dir, overwrite, creds, profile, debug, ignore_data, dry_run)


def _main():
    parser = argparse.ArgumentParser(
        description="Run WINGS template based on simulation matrix."
    )
    parser.add_argument(
        "-w",
        "--wings-config",
        dest="wings_config",
        required=True,
        help="WINGS Configuration File",
    )
    parser.add_argument(
        "-d", "--debug", dest="debug", default=False, action="store_true", help="Debug"
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", default=False, action="store_true", help="Dry run"
    )
    parser.add_argument("component_dir", help="Component Directory")
    args = parser.parse_args()

    if args.debug:
        os.environ["WINGS_DEBUG"] = "1"
    _utils.init_logger()

    deploy_component(**vars(args))


if __name__ == "__main__":
    try:
        _main()
        log.info("Done")
    except Exception as e:
        log.exception(e)
