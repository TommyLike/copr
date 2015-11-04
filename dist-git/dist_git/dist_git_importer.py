#!/usr/bin/python

import os
import json
import time
import shutil
import tempfile
import logging
from subprocess import PIPE, Popen, call

from requests import get, post

from .exceptions import PackageImportException, PackageDownloadException, PackageQueryException, GitAndTitoException
from .srpm_import import do_git_srpm_import

from .helpers import FailTypeEnum

log = logging.getLogger(__name__)


class SourceType:
    SRPM_LINK = 1
    SRPM_UPLOAD = 2
    GIT_AND_TITO = 3
    GIT_AND_MOCK = 4


class ImportTask(object):
    def __init__(self):

        self.task_id = None
        self.user = None
        self.project = None
        self.branch = None

        self.source_type = None
        self.source_json = None
        self.source_data = None

        self.package_name = None
        self.package_version = None
        self.git_hash = None

        # For SRPM_LINK and SRPM_UPLOAD
        self.package_url = None

        # For GIT_AND_TITO, GIT_AND_MOCK
        self.git_url = None
        self.git_branch = None

        # For GIT_AND_TITO
        self.tito_git_dir = None
        self.tito_test = None

        # For GIT_AND_MOCK
        self.mock_git_dir = None


    @property
    def reponame(self):
        if any(x is None for x in [self.user, self.project, self.package_name]):
            return None
        else:
            return "{}/{}/{}".format(self.user, self.project, self.package_name)

    @staticmethod
    def from_dict(dict_data, opts):
        task = ImportTask()

        task.task_id = dict_data["task_id"]
        task.user = dict_data["user"]
        task.project = dict_data["project"]

        task.branch = dict_data["branch"]
        task.source_type = dict_data["source_type"]
        task.source_json = dict_data["source_json"]
        task.source_data = json.loads(dict_data["source_json"])

        if task.source_type in [SourceType.GIT_AND_TITO, SourceType.GIT_AND_MOCK]:
            task.git_url = task.source_data["git_url"]
            task.git_branch = task.source_data["git_branch"]

        if task.source_type == SourceType.SRPM_LINK:
            task.package_url = json.loads(task.source_json)["url"]

        elif task.source_type == SourceType.SRPM_UPLOAD:
            json_tmp = task.source_data["tmp"]
            json_pkg = task.source_data["pkg"]
            task.package_url = "{}/tmp/{}/{}".format(opts.frontend_base_url, json_tmp, json_pkg)

        elif task.source_type == SourceType.GIT_AND_TITO:
            task.tito_git_dir = task.source_data["git_dir"]
            task.tito_test = task.source_data["tito_test"]

        elif task.source_type == SourceType.GIT_AND_MOCK:
            task.mock_git_dir = task.source_data["git_dir"]
            task.tito_git_dir = task.source_data["git_dir"]  # WORKAROUND

        else:
            raise PackageImportException("Got unknown source type: {}".format(task.source_type))

        return task

    def get_dict_for_frontend(self):
        return {
            "task_id": self.task_id,
            "pkg_name": self.package_name,
            "pkg_version": self.package_version,
            "repo_name": self.reponame,
            "git_hash": self.git_hash
        }


class SourceProvider(object):
    """
    Proxy to download sources and save them as SRPM
    """
    def __init__(self, task, target_path):
        """
        :param ImportTask task:
        :param str target_path:
        """
        self.task = task
        self.target_path = target_path

        if task.source_type == SourceType.SRPM_LINK:
            self.provider = SrpmUrlProvider

        elif task.source_type == SourceType.SRPM_UPLOAD:
            self.provider = SrpmUrlProvider

        elif task.source_type == SourceType.GIT_AND_TITO:
            self.provider = GitAndTitoProvider

        elif task.source_type == SourceType.GIT_AND_MOCK:
            self.provider = GitAndMockProvider

        else:
            raise PackageImportException("Got unknown source type: {}".format(task.source_type))

    def get_srpm(self):
        self.provider(self.task, self.target_path).get_srpm()


class BaseSourceProvider(object):
    def __init__(self, task, target_path):
        self.task = task
        self.target_path = target_path


class GitProvider(BaseSourceProvider):
    """
    Used for GIT_AND_TITO, GIT_AND_MOCK
    """
    def __init__(self, task, target_path):
        """
        :param ImportTask task:
        :param str target_path:
        :param function builder describe how to build SRPM from sources obtained via GIT:
        :raises PackageDownloadException:
        """
        # task.git_url
        # task.git_branch
        # task.tito_git_dir
        super(GitProvider, self).__init__(task, target_path)
        self.tmp = tempfile.mkdtemp()
        self.tmp_dest = tempfile.mkdtemp()
        self.git_subdir = None

    def get_srpm(self):
        self.clone()
        self.checkout()
        self.build()
        self.copy()
        self.clean()

    def clone(self):
        # 1. clone the repo
        log.debug("GIT_BUILDER: 1. clone".format(self.task.source_type))
        cmd = ['git', 'clone', self.task.git_url]
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.tmp)
            output, error = proc.communicate()
        except OSError as e:
            raise GitAndTitoException(FailTypeEnum("git_clone_failed"))
        if error:
            raise GitAndTitoException(FailTypeEnum("git_clone_failed"))

        # 1b. get dir name
        log.debug("GIT_BUILDER: 1b. dir name...")
        cmd = ['ls']
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.tmp)
            output, error = proc.communicate()
        except OSError as e:
            raise GitAndTitoException(FailTypeEnum("tito_wrong_directory_in_git"))
        if error:
            raise GitAndTitoException(FailTypeEnum("tito_wrong_directory_in_git"))
        if output and len(output.split()) == 1:
            git_dir_name = output.split()[0]
        else:
            raise GitAndTitoException(FailTypeEnum("tito_wrong_directory_in_git"))
        log.debug("   {}".format(git_dir_name))

        # @TODO Fix that ugly hack
        self.git_subdir = "{}/{}/{}".format(self.tmp, git_dir_name, self.task.tito_git_dir)

    def checkout(self):
        # 2. checkout git branch
        log.debug("GIT_BUILDER: 2. checkout")
        if self.task.git_branch and self.task.git_branch != 'master':
            cmd = ['git', 'checkout', self.task.git_branch]
            try:
                proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.git_subdir)
                output, error = proc.communicate()
            except OSError as e:
                raise GitAndTitoException(FailTypeEnum("tito_git_checkout_error"))
            if error:
                raise GitAndTitoException(FailTypeEnum("tito_git_checkout_error"))

    def build(self):
        raise NotImplemented

    def copy(self):
        # 4. copy srpm to the target destination
        log.debug("GIT_BUILDER: 4. get srpm path".format(self.task.source_type))
        dest_files = os.listdir(self.tmp_dest)
        dest_srpms = filter(lambda f: f.endswith(".src.rpm"), dest_files)
        if len(dest_srpms) == 1:
            srpm_name = dest_srpms[0]
        else:
            log.debug("ERROR :( :( :(")
            log.debug("git_subdir: {}".format(self.git_subdir))
            log.debug("dest_files: {}".format(dest_files))
            log.debug("dest_srpms: {}".format(dest_srpms))
            log.debug("")
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))
        log.debug("   {}".format(srpm_name))
        shutil.copyfile("{}/{}".format(self.tmp_dest, srpm_name), self.target_path)

    def clean(self):
        # 5. delete temps
        log.debug("GIT_BUILDER: 5. delete tmp")
        shutil.rmtree(self.tmp)
        shutil.rmtree(self.tmp_dest)


class GitAndTitoProvider(GitProvider):
    def build(self):
        # task.tito_test
        log.debug("GIT_BUILDER: 3. build via tito")
        cmd = ['tito', 'build', '-o', self.tmp_dest, '--srpm']
        if self.task.tito_test:
            cmd.append('--test')

        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=self.git_subdir)
            output, error = proc.communicate()
        except OSError as e:
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))
        if error:
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))


class GitAndMockProvider(GitProvider):
    def build(self):
        log.debug("GIT_BUILDER: 3. build via mock")

        specs = filter(lambda x: x.endswith(".spec"), os.listdir(self.git_subdir))
        if len(specs) != 1:
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))

        package_name = specs[0].replace(".spec", "")
        cmd = ['/usr/bin/mock', '-r', 'epel-7-x86_64',
               '--scm-enable',
               '--scm-option', 'method=git',
               '--scm-option', 'package={}'.format(package_name),
               '--scm-option', 'branch={}'.format(self.task.git_branch),
               '--scm-option', 'write_tar=True',
               '--scm-option', 'git_get="git clone {}"'.format(self.task.git_url),
               '--buildsrpm', '--resultdir={}'.format(self.tmp_dest)]

        try:
            proc = Popen(" ".join(cmd), shell=True, stdout=PIPE, stderr=PIPE, cwd=self.git_subdir)
            output, error = proc.communicate()
        except OSError as e:
            log.error(error)
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))
        if proc.returncode:
            log.error(error)
            raise GitAndTitoException(FailTypeEnum("tito_srpm_build_error"))


class SrpmUrlProvider(BaseSourceProvider):
    def get_srpm(self):
        """
        Used for SRPM_LINK and SRPM_UPLOAD
        :param ImportTask task:
        :param str target_path:
        :raises PackageDownloadException:
        """
        log.debug("download the package")
        try:
            r = get(self.task.package_url, stream=True, verify=False)
        except Exception:
            raise PackageDownloadException("Unexpected error during URL fetch: {}"
                                           .format(self.task.package_url))

        if 200 <= r.status_code < 400:
            try:
                with open(self.target_path, 'wb') as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
            except Exception:
                raise PackageDownloadException("Unexpected error during URL retrieval: {}"
                                               .format(self.task.package_url))
        else:
            raise PackageDownloadException("Failed to fetch: {} with HTTP status: {}"
                                           .format(self.task.package_url, r.status_code))


class DistGitImporter(object):
    def __init__(self, opts):
        self.is_running = False
        self.opts = opts

        self.get_url = "{}/backend/importing/".format(self.opts.frontend_base_url)
        self.upload_url = "{}/backend/import-completed/".format(self.opts.frontend_base_url)
        self.auth = ("user", self.opts.frontend_auth)
        self.headers = {"content-type": "application/json"}

        self.tmp_root = None

    def try_to_obtain_new_task(self):
        log.debug("1. Try to get task data")
        try:
            # get the data
            r = get(self.get_url)
            # take the first task
            builds_list = r.json()["builds"]
            if len(builds_list) == 0:
                log.debug("No new tasks to process")
                return
            return ImportTask.from_dict(builds_list[0], self.opts)
        except Exception:
            log.exception("Failed acquire new packages for import")
        return

    def git_import_srpm(self, task, filepath):
        """
        Imports a source rpm file into local dist git.
        Repository name is in the Copr Style: user/project/package
        filepath is a srpm file locally downloaded somewhere

        :type task: ImportTask
        """
        log.debug("importing srpm into the dist-git")

        tmp = tempfile.mkdtemp()
        try:
            return do_git_srpm_import(self.opts, filepath, task, tmp)
        finally:
            shutil.rmtree(tmp)

    @staticmethod
    def pkg_name_evr(srpm_path):
        """
        Queries a package for its name and evr (epoch:version-release)
        """
        log.debug("Verifying packagage, getting  name and version.")
        cmd = ['rpm', '-qp', '--nosignature', '--qf', '%{NAME} %{EPOCH} %{VERSION} %{RELEASE}', srpm_path]
        try:
            proc = Popen(cmd, stdout=PIPE, stderr=PIPE)
            output, error = proc.communicate()
        except OSError as e:
            raise PackageQueryException(e)
        if error:
            raise PackageQueryException('Error querying srpm: %s' % error)

        try:
            name, epoch, version, release = output.split(" ")
        except ValueError as e:
            raise PackageQueryException(e)

        # Epoch is an integer or '(none)' if not set
        if epoch.isdigit():
            evr = "{}:{}-{}".format(epoch, version, release)
        else:
            evr = "{}-{}".format(version, release)

        return name, evr

    def after_git_import(self):
        log.debug("refreshing cgit listing")
        call(["/usr/share/dist-git/cgit_pkg_list.sh", self.opts.cgit_pkg_list_location])

    @staticmethod
    def before_git_import(task):
        log.debug("make sure repos exist: {}".format(task.reponame))
        call(["/usr/share/dist-git/git_package.sh", task.reponame])
        call(["/usr/share/dist-git/git_branch.sh", task.branch, task.reponame])

    def post_back(self, data_dict):
        """
        Could raise error related to networkd connection
        """
        log.debug("Sending back: \n{}".format(json.dumps(data_dict)))
        return post(self.upload_url, auth=self.auth, data=json.dumps(data_dict), headers=self.headers)

    def post_back_safe(self, data_dict):
        """
        Ignores any error
        """
        try:
            return self.post_back(data_dict)
        except Exception:
            log.exception("Failed to post back to frontend : {}".format(data_dict))

    def do_import(self, task):
        """
        :type task: ImportTask
        """
        log.info("2. Task: {}, importing the package: {}"
                 .format(task.task_id, task.package_url))
        tmp_root = tempfile.mkdtemp()
        fetched_srpm_path = os.path.join(tmp_root, "package.src.rpm")

        try:
            SourceProvider(task, fetched_srpm_path).get_srpm()
            task.package_name, task.package_version = self.pkg_name_evr(fetched_srpm_path)

            self.before_git_import(task)
            task.git_hash = self.git_import_srpm(task, fetched_srpm_path)
            self.after_git_import()

            log.debug("sending a response - success")
            self.post_back(task.get_dict_for_frontend())

        except PackageImportException:
            log.exception("send a response - failure during import of: {}".format(task.package_url))
            self.post_back_safe({"task_id": task.task_id, "error": "git_import_failed"})

        except PackageDownloadException:
            log.exception("send a response - failure during download of: {}".format(task.package_url))
            self.post_back_safe({"task_id": task.task_id, "error": "srpm_download_failed"})

        except PackageQueryException:
            log.exception("send a response - failure during query of: {}".format(task.package_url))
            self.post_back_safe({"task_id": task.task_id, "error": "srpm_query_failed"})

        except GitAndTitoException as e:
            log.exception("send a response - failure during 'Tito and Git' import of: {}".format(task.git_url))
            log.exception("   ... due to: {}".format(str(e)))
            self.post_back_safe({"task_id": task.task_id, "error": str(e)})

        except Exception:
            log.exception("Unexpected error during package import")
            self.post_back_safe({"task_id": task.task_id, "error": "unknown_error"})

        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def run(self):
        log.info("DistGitImported initialized")

        self.is_running = True
        while self.is_running:
            mb_task = self.try_to_obtain_new_task()
            if mb_task is None:
                time.sleep(self.opts.sleep_time)
            else:
                self.do_import(mb_task)
