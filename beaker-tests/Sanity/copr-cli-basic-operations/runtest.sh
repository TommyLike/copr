#!/bin/bash
# vim: dict=/usr/share/beakerlib/dictionary.vim cpt=.,w,b,u,t,i,k
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   runtest.sh of /tools/copr/Sanity/copr-cli-basic-operations
#   Description: Tests basic operations of copr using copr-cli.
#   Author: Adam Samalik <asamalik@redhat.com>
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright (c) 2014 Red Hat, Inc.
#
#   This program is free software: you can redistribute it and/or
#   modify it under the terms of the GNU General Public License as
#   published by the Free Software Foundation, either version 2 of
#   the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be
#   useful, but WITHOUT ANY WARRANTY; without even the implied
#   warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
#   PURPOSE.  See the GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program. If not, see http://www.gnu.org/licenses/.
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

dnf install jq

# Include Beaker environment
. /usr/bin/rhts-environment.sh || exit 1
. /usr/share/beakerlib/beakerlib.sh || exit 1

PACKAGE="copr"
# we (the mankind) need the names to be unique
NAME_PREFIX="@copr/TEST$(date +%s)"

rlJournalStart
    rlPhaseStartSetup
        rlAssertRpm "copr-cli"
        rlAssertExists ~/.config/copr
        # testing instance?
        rlAssertGrep "copr-fe-dev.cloud.fedoraproject.org" ~/.config/copr
        # we don't need to be destroying the production instance
        rlAssertNotGrep "copr.fedoraproject.org" ~/.config/copr
        # token ok? communication ok?
        rlRun "copr-cli list"
        # and install... things
        yum -y install dnf dnf-plugins-core
        # use the dev instance
        sed -i "s/http:\/\/copr.fedoraproject.org/http:\/\/copr-fe-dev.cloud.fedoraproject.org/g" \
        /usr/lib/python3.4/site-packages/dnf-plugins/copr.py
        sed -i "s/https:\/\/copr.fedoraproject.org/http:\/\/copr-fe-dev.cloud.fedoraproject.org/g" \
        /usr/lib/python3.4/site-packages/dnf-plugins/copr.py
    rlPhaseEnd

    rlPhaseStartTest
        ### ---- CREATING PROJECTS ------ ###
        # create - OK
        rlRun "copr-cli create --chroot fedora-23-x86_64 ${NAME_PREFIX}Project1"
        # create - the same name again
        rlRun "copr-cli create --chroot fedora-23-x86_64 ${NAME_PREFIX}Project1" 1
        # create - wrong chroot name
        rlRun "copr-cli create --chroot wrong-chroot-name ${NAME_PREFIX}Project2" 1
        # create second project
        rlRun "copr-cli create --chroot fedora-23-x86_64 --repo 'copr://${NAME_PREFIX}Project1' ${NAME_PREFIX}Project2"
        # create third project
        rlRun "copr-cli create --chroot fedora-23-x86_64 --repo 'copr://${NAME_PREFIX}Project1' ${NAME_PREFIX}Project3"
        ### left after this section: Project1, Project2, Project3

        ### ---- BUILDING --------------- ###
        # build - wrong project name
        rlRun "copr-cli build ${NAME_PREFIX}wrong-name http://nowhere/nothing.src.rpm" 1
        # build - wrong chroot name
        rlRun "copr-cli build -r wrong-chroot-name ${NAME_PREFIX}Project1 http://nowhere/nothing.src.rpm" 1
        # build - OK
        rlRun "copr-cli build ${NAME_PREFIX}Project1 http://asamalik.fedorapeople.org/hello-2.8-1.fc20.src.rpm"
        # build - the same version modified - SKIPPED
        rlRun "copr-cli build ${NAME_PREFIX}Project1 http://asamalik.fedorapeople.org/changed/hello-2.8-1.fc20.src.rpm"
        # build - FAIL  (syntax error in source code)
        rlRun "copr-cli build ${NAME_PREFIX}Project1 http://asamalik.fedorapeople.org/evilhello-2.8-1.fc20.src.rpm" 4
        # enable Project1 repo
        rlRun "yes | dnf copr enable ${NAME_PREFIX}Project1 fedora-23-x86_64"
        # install hello package
        rlRun "dnf install -y hello"
        # and check wheter it's installed
        rlAssertRpm "hello"
        # run it
        rlRun "hello"
        # check if we have the first package and not the skipped one
        rlRun "hello | grep changed" 1
        ### left after this section: Project1, hello installed

        ## test auto_createrepo property of copr-project using Project2
        # remove hello
        rlRun "dnf remove hello -y"
        # disable Project1 repo
        rlRun "yes | dnf copr disable $NAME_PREFIX\"Project1\""
        # disable auto_createrepo
        rlRun "copr-cli modify --disable_createrepo true ${NAME_PREFIX}Project2"
        # build 1st package
        rlRun "copr-cli build ${NAME_PREFIX}Project2 http://asamalik.fedorapeople.org/hello-2.8-1.fc20.src.rpm"
        # enable Project2 repo
        rlRun "yes | dnf copr enable ${NAME_PREFIX}Project2 fedora-23-x86_64"
        # try to install - FAIL ( public project metadata not updated)
        rlRun "dnf install -y hello" 1
        # build 2nd package ( requires 1st package for the build)
        rlRun "copr-cli build ${NAME_PREFIX}Project2 https://frostyx.fedorapeople.org/hello_beaker_test_2-0.0.1-1.fc22.src.rpm"
        # try to install - FAIL ( public project metadata not updated)
        rlRun "dnf install -y hello_beaker_test_2" 1
        # re-enabling metadata generation
        rlRun "copr-cli modify --disable_createrepo false ${NAME_PREFIX}Project2"
        # waiting for action to complete
        sleep 120
        # trying to install
        rlRun "dnf install -y --refresh hello_beaker_test_2"

        ## test build watching using Project3
        # build 1st package without waiting
        rlRun "copr-cli build --nowait ${NAME_PREFIX}Project3 http://asamalik.fedorapeople.org/hello-2.8-1.fc20.src.rpm > hello_p3.out"
        rlRun "awk '/Created build/ { print \$3 }' hello_p3.out > hello_p3.id"
        # initial status should be in progress, e.g. pending/running
        rlRun "xargs copr-cli status < hello_p3.id | grep -v succeeded"
        # wait for the build to complete and ensure it succeeded
        rlRun "xargs copr-cli watch-build < hello_p3.id"
        rlRun "xargs copr-cli status < hello_p3.id | grep succeeded"

        ## test package creation and editing
        OUTPUT=`mktemp`
        SOURCE_JSON=`mktemp`

        # create special repo for our test
        rlRun "copr-cli create --chroot fedora-23-x86_64 ${NAME_PREFIX}Project4"

        # invalid package data
        rlRun "copr-cli add-package-tito ${NAME_PREFIX}Project4 --name test_package_tito --git-url invalid_url" 1

        # tito package creation
        rlRun "copr-cli add-package-tito ${NAME_PREFIX}Project4 --name test_package_tito --git-url http://github.com/clime/example.git --test on --webhook-rebuild on --git-branch foo --git-dir bar"
        rlRun "copr-cli get-package ${NAME_PREFIX}Project4 --name test_package_tito > $OUTPUT"
        cat $OUTPUT | jq '.source_json' | sed -r 's/"(.*)"/\1/g' | sed -r 's/\\(.)/\1/g' > $SOURCE_JSON
        rlAssertEquals "package.name == \"test_package_tito\"" `cat $OUTPUT | jq '.name'` '"test_package_tito"'
        rlAssertEquals "package.webhook_rebuild == \"true\"" `cat $OUTPUT | jq '.webhook_rebuild'` 'true'
        rlAssertEquals "package.source_json.tito_test == true" `cat $SOURCE_JSON | jq '.tito_test'` 'true'
        rlAssertEquals "package.source_json.git_url == \"http://github.com/clime/example.git\"" `cat $SOURCE_JSON | jq '.git_url'` '"http://github.com/clime/example.git"'
        rlAssertEquals "package.source_json.git_branch == \"foo\"" `cat $SOURCE_JSON | jq '.git_branch'` '"foo"'
        rlAssertEquals "package.source_json.git_dir == \"bar\"" `cat $SOURCE_JSON | jq '.git_dir'` '"bar"'

        # tito package editing
        rlRun "copr-cli edit-package-tito ${NAME_PREFIX}Project4 --name test_package_tito --git-url http://github.com/clime/example2.git --test off --webhook-rebuild off --git-branch bar --git-dir foo"
        rlRun "copr-cli get-package ${NAME_PREFIX}Project4 --name test_package_tito > $OUTPUT"
        cat $OUTPUT | jq '.source_json' | sed -r 's/"(.*)"/\1/g' | sed -r 's/\\(.)/\1/g' > $SOURCE_JSON
        rlAssertEquals "package.name == \"test_package_tito\"" `cat $OUTPUT | jq '.name'` '"test_package_tito"'
        rlAssertEquals "package.webhook_rebuild == \"false\"" `cat $OUTPUT | jq '.webhook_rebuild'` 'false'
        rlAssertEquals "package.source_json.tito_test == false" `cat $SOURCE_JSON | jq '.tito_test'` 'false'
        rlAssertEquals "package.source_json.git_url == \"http://github.com/clime/example2.git\"" `cat $SOURCE_JSON | jq '.git_url'` '"http://github.com/clime/example2.git"'
        rlAssertEquals "package.source_json.git_branch == \"bar\"" `cat $SOURCE_JSON | jq '.git_branch'` '"bar"'
        rlAssertEquals "package.source_json.git_dir == \"foo\"" `cat $SOURCE_JSON | jq '.git_dir'` '"foo"'

        # package listing
        rlAssertEquals "len(package_list) == 1" `copr-cli list-packages ${NAME_PREFIX}Project4 | jq '. | length'` 1

        # pypi package creation
        rlRun "./copr add-package-pypi ${NAME_PREFIX}Project4 --name test_package_pypi --packagename pyp2rpm --packageversion 1.5 --pythonversions 3 2"
        rlRun "./copr get-package ${NAME_PREFIX}Project4 --name test_package_pypi > $OUTPUT"
        cat $OUTPUT | jq '.source_json' | sed -r 's/"(.*)"/\1/g' | sed -r 's/\\(.)/\1/g' > $SOURCE_JSON
        rlAssertEquals "package.name == \"test_package_pypi\"" `cat $OUTPUT | jq '.name'` '"test_package_pypi"'
        rlRun `cat $SOURCE_JSON | jq '.python_versions == ["3", "2"]'` 0 "package.source_json.python_versions == [\"3\", \"2\"]"
        rlAssertEquals "package.source_json.pypi_package_name == \"pyp2rpm\"" `cat $SOURCE_JSON | jq '.pypi_package_name'` '"pyp2rpm"'
        rlAssertEquals "package.source_json.pypi_package_version == \"bar\"" `cat $SOURCE_JSON | jq '.pypi_package_version'` '"1.5"'
        
        # pypi package editing
        rlRun "./copr edit-package-pypi ${NAME_PREFIX}Project4 --name test_package_pypi --packagename motionpaint --packageversion 1.4 --pythonversions 2 3"
        rlRun "./copr get-package ${NAME_PREFIX}Project4 --name test_package_pypi > $OUTPUT"
        cat $OUTPUT | jq '.source_json' | sed -r 's/"(.*)"/\1/g' | sed -r 's/\\(.)/\1/g' > $SOURCE_JSON
        rlAssertEquals "package.name == \"test_package_pypi\"" `cat $OUTPUT | jq '.name'` '"test_package_pypi"'
        rlRun `cat $SOURCE_JSON | jq '.python_versions == ["2", "3"]'` 0 "package.source_json.python_versions == [\"2\", \"3\"]"
        rlAssertEquals "package.source_json.pypi_package_name == \"motionpaint\"" `cat $SOURCE_JSON | jq '.pypi_package_name'` '"motionpaint"'
        rlAssertEquals "package.source_json.pypi_package_version == \"bar\"" `cat $SOURCE_JSON | jq '.pypi_package_version'` '"1.4"'

        # package listing
        rlAssertEquals "len(package_list) == 2" `copr-cli list-packages ${NAME_PREFIX}Project4 | jq '. | length'` 2

        ### ---- DELETING PROJECTS ------- ###
        # delete - wrong project name
        rlRun "copr-cli delete ${NAME_PREFIX}wrong-name" 1
        # delete the projects
        rlRun "copr-cli delete ${NAME_PREFIX}Project1"
        rlRun "copr-cli delete ${NAME_PREFIX}Project2"
        rlRun "copr-cli delete ${NAME_PREFIX}Project3"
        # and make sure we haven't left any mess
        rlRun "copr-cli list | grep $NAME_PREFIX" 1
        ### left after this section: hello installed
    rlPhaseEnd

    rlPhaseStartCleanup
    rlPhaseEnd
rlJournalPrintText
rlJournalEnd
