import argparse
import concurrent.futures
from contextlib import contextmanager
import yaml
import logging
import json
import wings
import click
import os
import base64
import zipfile
import requests
import shutil
from wcm import _schema, _utils

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


def check_overwrite(path, overwrite):
    # Checks if file already exists
    if os.path.exists(path):
        logger.info("\"" + path + "\" already exists")
        if overwrite:
            logger.info("Overwriting existing file")
            shutil.rmtree(path)
        else:
            logger.error("Downloading this component would overwrite the existing one. "
                         "To force download use flag -f")
            logger.info("Aborting Download")
            exit(0)


def wings_download(path, comp_id, profile, overwrite):
    with _cli(profile=profile) as wings_instance:
        component = wings_instance.component.get_component_description(comp_id)

        if component is None:
            logger.error("Invalid ID: \"" + comp_id + "\"")
            exit(1)

        # Make new folder to put everything in
        path = os.path.join(path, comp_id)
        check_overwrite(path, overwrite)  # check if an existing file is already there
        os.mkdir(path)

        wings_instance.component.download_component(comp_id, os.path.join(path, "components"))

        yaml_data = {}
        data_types = {}

        yaml_data["name"] = ""
        yaml_data["version"] = ""
        # yaml_data["#description"] = None
        # yaml_data["#keywords"] = None
        # yaml_data["homepage"] = None
        # amlData["license"] = None
        # yaml_data["author"] = None
        # yaml_data["container"] = None
        # yaml_data["repository"] = None
        yaml_data["schemaVersion"] = _schema.get_schema_version()
        yaml_data["wings"] = component
        component = yaml_data["wings"]

        # takes the id and splits it by the '#' sign
        # (id example: http://localhost:8080/export/users/mint/api-test/components/library.owl#HAND-1)
        info = component["id"].split("#")
        info = info[len(info) - 1]  # gets the last index of the split (ie: HAND-1)
        info = info.split("-")  # splits it by the '-' (ie {"HAND","1"})

        # First part becomes name, other becomes version
        yaml_data["name"] = info[0]
        if len(info) > 1:
            yaml_data["version"] = info[-1]
        else:
            logger.warning("No version could be ascertained from the name")

        try:
            component.pop("location")
            component.pop("id")
            component.pop("type")
            component["documentation"] = component["documentation"].strip()
            component["files"] = ["src\\*"]
        except KeyError:
            logger.warning("Component seems to be missing metadata")

        # loops through every input field
        if len(component["inputs"]) <= 0:
            logger.warning("Component has no inputs")
        for i in (component["inputs"]):
            files = {}
            i.pop("id")
            try:
                if "XMLSchema" not in (i["type"]):
                    type_name = i["type"].split("#")
                    type_name = type_name[len(type_name) - 1]

                    i["type"] = "dcdom:" + type_name
                    files["files"] = []
                    data_types[type_name] = files
            except:
                logger.warning("no type in " + str(i))

        component["data"] = data_types

        if len(component["outputs"]) <= 0:
            logger.warning("Component has no outputs")
        for o in (component["outputs"]):
            files = {}
            o.pop("id")
            try:
                if "XMLSchema" not in o["type"]:
                    type_name = o["type"].split("#")
                    type_name = type_name[len(type_name) - 1]

                    o["type"] = "dcdom:" + type_name
                    files["files"] = []
                    data_types[type_name] = files
            except:
                logger.warning("no type in " + str(o))

        # makes the YAML file
        stream = open(os.path.join(path, "wings-component.yaml"), 'w+')
        yaml.dump(yaml_data, stream, sort_keys=False)

        logger.info("Generated YAML")

        # makes the src folder in the directory
        try:
            os.mkdir(os.path.join(path, "src"))
        except FileExistsError:
            logger.warning("src folder already exists")

        data_path = os.path.join(path, "data")

        try:
            os.mkdir(data_path)
        except FileExistsError:
            logger.warning("data folder already exists")

        logger.info("Extracting source code")
        # unzip components
        comp_os_path = ""
        try:
            comp_os_path = os.path.join(path, "components")
            zip_path = os.path.join(comp_os_path, comp_id + ".zip")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(comp_os_path)
        except zipfile.BadZipFile:
            logger.error("Downloaded zip file seems to be corrupt")
            exit(1)

        # copy files into src folder
        comp_files = os.listdir(os.path.join(comp_os_path, comp_id))
        for files in comp_files:
            full_file_name = os.path.join(os.path.join(comp_os_path, comp_id), files)
            if os.path.isfile(full_file_name):
                shutil.copy(full_file_name, os.path.join(path, "src"))

        # remove component folder
        shutil.rmtree(comp_os_path)

        logger.info("Download complete")


def github_download(path, comp_id, profile, overwrite):
    repo = "mintproject/wcm-components"
    session = requests.Session()

    # get the gitHub api credentials from the wcm credentials file
    creds = _utils.github_credentials(profile)

    if len(creds) > 0:
        # gives session credentials
        username = creds[0]  # for authentication and committing
        token = creds[1]
        session.auth = (username, token)
    else:
        logger.error("Could not authenticate GitHub credentials from wcm credentials file")
        logger.info("Cannot download component without proper credentials")
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
        tree = {}
        logger.error("Something went wrong accessing GitHub. Maybe the GitHub username or token is incorrect?")
        exit(1)

    # Once the tree, authentication and session are set up start trying to download
    mod_ver = comp_id.split("-")
    model = ""
    version = ""

    # Checks if user gave a component with version number or not
    if len(mod_ver) == 1:
        model = mod_ver[0]
    elif len(mod_ver) == 2:
        model = mod_ver[0]
        version = mod_ver[1]

    # Attempts to find the given component within the tree
    for i in tree:
        if i['path'] == model:
            url = i['url']
            git_dir = session.get(url)
            git_dir = json.loads(git_dir.text)
            model = {}

            # If there is no version number just download the first indexed version from the GitHub repo
            if version == "":
                logger.info("No component version given.")
                version = (git_dir['tree'])[-1]
                model = version
                logger.info("Using version: " + model["path"].split(".")[0])

            # If there is a version number given and it is found use that one
            else:
                for j in git_dir['tree']:
                    if j['path'] == version or j['path'] == (version + ".zip"):
                        model = j

            if model == {}:
                logger.error("Incorrect model name or version")
                exit(1)

            content = session.get(model['url'])
            content = json.loads(content.text)

            dir_name = i["path"] + "-" + model["path"].split(".")[0]

            path = os.path.join(path, dir_name)
            check_overwrite(path, overwrite)

            os.mkdir(path)  # make directory for zip

            # Try to download zipped file from GitHub
            try:
                # base64 encoded file
                zip_file = content["content"]
                decode = base64.b64decode(zip_file)
                print(os.path.join(path, dir_name + ".zip"))
                with open(os.path.join(path, dir_name + ".zip"), 'wb') as f:
                    f.write(decode)

                # Unzip File
                try:
                    zip_path = os.path.join(path, dir_name + ".zip")
                    with zipfile.ZipFile(zip_path, "r") as zip_ref:
                        zip_ref.extractall(path)
                except zipfile.BadZipFile:
                    logger.error("Downloaded zip file seems to be corrupt")
                    exit(1)

                # Remove zipped component
                os.remove(zip_path)

                logger.info("Download Complete")
                return True
            except KeyError:
                logger.error("Requested file from GitHub cannot be downloaded. Maybe it is not zipped?")
                exit(1)

    logger.error("Could not find component with that name")


def download(component_dir, profile=None, download_path=None, overwrite=False, wings=False):

    comp_id = component_dir

    # sets path, this determines where the component will be downloaded. Default is the current directory of the program
    if download_path is None:
        path = os.getcwd()
    else:
        path = download_path

    # If user specifies to download from wings server
    if wings:
        logger.info("Using Wings")
        wings_download(path, comp_id, profile, overwrite)

    # else download from GitHub
    else:
        logger.info("Using GitHub")
        github_download(path, comp_id, profile, overwrite)



def _main():
    parser = argparse.ArgumentParser(
        description="Download wings components given the component id."
    )
    parser.add_argument(
        "--file-path",
        "-f",
        type=str,
        default=None,
    )
    parser.add_argument("-c", "--component", required=True, help="Component name to download")
    parser.add_argument(
        "-d", "--debug", dest="debug", default=False, action="store_true", help="Debug"
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", default=False, action="store_true", help="Dry run"
    )
    args = parser.parse_args()

    if args.debug:
        os.environ["WINGS_DEBUG"] = "1"
    _utils.init_logger()
    component_dir = args.component
    file_path = args.file_path
    download(component_dir, file_path)


if __name__ == "__main__":
    try:
        _main()
    except Exception as e:
        logger.exception(e)
