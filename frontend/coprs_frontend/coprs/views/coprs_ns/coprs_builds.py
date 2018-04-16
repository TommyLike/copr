import flask
from flask import request, render_template, url_for, stream_with_context
import re
import os
import shutil
import tempfile

from functools import wraps
from werkzeug import secure_filename

from coprs import app
from coprs import db
from coprs import forms
from coprs import helpers

from coprs.logic import builds_logic
from coprs.logic import coprs_logic
from coprs.logic.builds_logic import BuildsLogic
from coprs.logic.complex_logic import ComplexLogic

from coprs.views.misc import (login_required, page_not_found, req_with_copr,
        req_with_copr, send_build_icon)
from coprs.views.coprs_ns import coprs_ns

from coprs.exceptions import (ActionInProgressException,
                              InsufficientRightsException,
                              UnrepeatableBuildException)


@coprs_ns.route("/build/<int:build_id>/")
def copr_build_redirect(build_id):
    build = ComplexLogic.get_build_safe(build_id)
    copr = build.copr
    return flask.redirect(helpers.copr_url("coprs_ns.copr_build", copr, build_id=build_id))


@coprs_ns.route("/build/<int:build_id>/status_image.png")
def copr_build_icon(build_id):
    return send_build_icon(BuildsLogic.get_by_id(int(build_id)).first())


################################ Build detail ################################

@coprs_ns.route("/<username>/<coprname>/build/<int:build_id>/")
@coprs_ns.route("/g/<group_name>/<coprname>/build/<int:build_id>/")
@req_with_copr
def copr_build(copr, build_id):
    return render_copr_build(build_id, copr)


def render_copr_build(build_id, copr):
    build = ComplexLogic.get_build_safe(build_id)
    return render_template("coprs/detail/build.html", build=build, copr=copr)


################################ Build table ################################

@coprs_ns.route("/<username>/<coprname>/builds/")
@coprs_ns.route("/g/<group_name>/<coprname>/builds/")
@req_with_copr
def copr_builds(copr):
    return render_copr_builds(copr)


def render_copr_builds(copr):
    flashes = flask.session.pop('_flashes', [])
    builds_query = builds_logic.BuildsLogic.get_copr_builds_list(copr=copr)
    response = flask.Response(stream_with_context(helpers.stream_template("coprs/detail/builds.html",
                                 copr=copr,
                                 builds=list(builds_query),
                                 flashes=flashes,
                                 )))

    flask.session.pop('_flashes', [])
    app.save_session(flask.session, response)
    return response

################################ Url builds ################################

@coprs_ns.route("/<username>/<coprname>/add_build/")
@coprs_ns.route("/g/<group_name>/<coprname>/add_build/")
@login_required
@req_with_copr
def copr_add_build(copr, form=None):
    return render_add_build(
        copr, form, view='coprs_ns.copr_new_build')


def render_add_build(copr, form, view):
    if not form:
        form = forms.BuildFormUrlFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/url.html",
                                 copr=copr, view=view, form=form)


@coprs_ns.route("/<username>/<coprname>/new_build/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build(copr):
    return process_new_build_url(
        copr,
        "coprs_ns.copr_new_build",
        url_on_success=helpers.copr_url("coprs_ns.copr_builds", copr))


def process_new_build_url(copr, add_view, url_on_success):
    def factory(**build_options):
        pkgs = form.pkgs.data.split("\n")
        for pkg in pkgs:
            BuildsLogic.create_new_from_url(
                flask.g.user, copr, pkg,
                chroot_names=form.selected_chroots,
                **build_options
            )
        for pkg in pkgs:
            flask.flash("New build has been created: {}".format(pkg), "success")

    form = forms.BuildFormUrlFactory(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build,
                             add_view, url_on_success, msg_on_success=False)


def process_new_build(copr, form, create_new_build_factory, add_function, add_view, url_on_success, msg_on_success=True):
    if form.validate_on_submit():
        build_options = {
            "enable_net": form.enable_net.data,
            "timeout": form.timeout.data,
        }

        try:
            create_new_build_factory(**build_options)
            db.session.commit()
        except (ActionInProgressException, InsufficientRightsException, UnrepeatableBuildException) as e:
            db.session.rollback()
            flask.flash(str(e), "error")
        else:
            if msg_on_success:
                flask.flash("New build has been created.", "success")

        return flask.redirect(url_on_success)
    else:
        return add_function(copr, form, add_view)


################################ SCM builds #########################################

@coprs_ns.route("/<username>/<coprname>/add_build_scm/")
@coprs_ns.route("/g/<group_name>/<coprname>/add_build_scm/")
@login_required
@req_with_copr
def copr_add_build_scm(copr, form=None):
    return render_add_build_scm(
        copr, form, view='coprs_ns.copr_new_build_scm')


def render_add_build_scm(copr, form, view, package=None):
    if not form:
        form = forms.BuildFormScmFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/scm.html",
                                 copr=copr, form=form, view=view, package=package)


@coprs_ns.route("/<username>/<coprname>/new_build_scm/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build_scm/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_scm(copr):
    view = 'coprs_ns.copr_new_build_scm'
    url_on_success = helpers.copr_url("coprs_ns.copr_builds", copr)
    return process_new_build_scm(copr, view, url_on_success)


def process_new_build_scm(copr, add_view, url_on_success):
    def factory(**build_options):
        BuildsLogic.create_new_from_scm(
            flask.g.user,
            copr,
            form.scm_type.data,
            form.clone_url.data,
            form.committish.data,
            form.subdirectory.data,
            form.spec.data,
            form.srpm_build_method.data,
            form.selected_chroots,
            **build_options
        )
    form = forms.BuildFormScmFactory(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build_scm, add_view, url_on_success)


################################ PyPI builds ################################

@coprs_ns.route("/<username>/<coprname>/add_build_pypi/")
@coprs_ns.route("/g/<group_name>/<coprname>/add_build_pypi/")
@login_required
@req_with_copr
def copr_add_build_pypi(copr, form=None):
    return render_add_build_pypi(
        copr, form, view='coprs_ns.copr_new_build_pypi')


def render_add_build_pypi(copr, form, view, package=None):
    if not form:
        form = forms.BuildFormPyPIFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/pypi.html",
                                 copr=copr, form=form, view=view, package=package)


@coprs_ns.route("/<username>/<coprname>/new_build_pypi/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build_pypi/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_pypi(copr):
    view = 'coprs_ns.copr_new_build_pypi'
    url_on_success = helpers.copr_url("coprs_ns.copr_builds", copr)
    return process_new_build_pypi(copr, view, url_on_success)


def process_new_build_pypi(copr, add_view, url_on_success):
    def factory(**build_options):
        BuildsLogic.create_new_from_pypi(
            flask.g.user,
            copr,
            form.pypi_package_name.data,
            form.pypi_package_version.data,
            form.python_versions.data,
            form.selected_chroots,
            **build_options
        )
    form = forms.BuildFormPyPIFactory(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build_pypi, add_view, url_on_success)


############################### RubyGems builds ###############################

@coprs_ns.route("/<username>/<coprname>/add_build_rubygems/")
@coprs_ns.route("/g/<group_name>/<coprname>/add_build_rubygems/")
@login_required
@req_with_copr
def copr_add_build_rubygems(copr, form=None):
    return render_add_build_rubygems(
        copr, form, view='coprs_ns.copr_new_build_rubygems')


def render_add_build_rubygems(copr, form, view, package=None):
    if not form:
        form = forms.BuildFormRubyGemsFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/rubygems.html",
                                 copr=copr, form=form, view=view, package=package)


@coprs_ns.route("/<username>/<coprname>/new_build_rubygems/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build_rubygems/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_rubygems(copr):
    view = 'coprs_ns.copr_new_build_rubygems'
    url_on_success = helpers.copr_url("coprs_ns.copr_builds", copr)
    return process_new_build_rubygems(copr, view, url_on_success)


def process_new_build_rubygems(copr, add_view, url_on_success):
    def factory(**build_options):
        BuildsLogic.create_new_from_rubygems(
            flask.g.user,
            copr,
            form.gem_name.data,
            form.selected_chroots,
            **build_options
        )
    form = forms.BuildFormRubyGemsFactory(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build_rubygems, add_view, url_on_success)

############################### Custom builds ###############################

@coprs_ns.route("/g/<group_name>/<coprname>/new_build_custom/", methods=["POST"])
@coprs_ns.route("/<username>/<coprname>/new_build_custom/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_custom(copr):
    """ Handle the build request and redirect back. """

    # TODO: parametric decorator for this view && url_on_success
    view = 'coprs_ns.copr_new_build_custom'
    url_on_success = helpers.copr_url('coprs_ns.copr_add_build_custom', copr)

    def factory(**build_options):
        BuildsLogic.create_new_from_custom(
            flask.g.user,
            copr,
            form.script.data,
            form.chroot.data,
            form.builddeps.data,
            form.resultdir.data,
            chroot_names=form.selected_chroots,
            **build_options
        )

    form = forms.BuildFormCustomFactory(copr.active_chroots)()

    return process_new_build(copr, form, factory, render_add_build_custom,
                             view, url_on_success)



@coprs_ns.route("/g/<group_name>/<coprname>/add_build_custom/")
@coprs_ns.route("/<username>/<coprname>/add_build_custom/")
@login_required
@req_with_copr
def copr_add_build_custom(copr, form=None):
    return render_add_build_custom(copr, form,
                                   'coprs_ns.copr_new_build_custom')

def render_add_build_custom(copr, form, view, package=None):
    if not form:
        form = forms.BuildFormCustomFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/custom.html",
                                 copr=copr, form=form, view=view)


################################ Upload builds ################################

@coprs_ns.route("/<username>/<coprname>/add_build_upload/")
@coprs_ns.route("/g/<group_name>/<coprname>/add_build_upload/")
@login_required
@req_with_copr
def copr_add_build_upload(copr, form=None):
    return render_add_build_upload(
        copr, form, view='coprs_ns.copr_new_build_upload')


def render_add_build_upload(copr, form, view):
    if not form:
        form = forms.BuildFormUploadFactory(copr.active_chroots)()
    return flask.render_template("coprs/detail/add_build/upload.html",
                                 copr=copr, form=form, view=view)


@coprs_ns.route("/<username>/<coprname>/new_build_upload/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build_upload/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_upload(copr):
    view = 'coprs_ns.copr_new_build_upload'
    url_on_success = helpers.copr_url("coprs_ns.copr_builds", copr)
    return process_new_build_upload(copr, view, url_on_success)


def process_new_build_upload(copr, add_view, url_on_success):
    def factory(**build_options):
        BuildsLogic.create_new_from_upload(
            flask.g.user, copr,
            f_uploader=lambda path: form.pkgs.data.save(path),
            orig_filename=form.pkgs.data.filename,
            chroot_names=form.selected_chroots,
            **build_options
        )
    form = forms.BuildFormUploadFactory(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build_upload, add_view, url_on_success)


################################ Builds rebuilds ################################

@coprs_ns.route("/<username>/<coprname>/new_build_rebuild/<int:build_id>/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/new_build_rebuild/<int:build_id>/", methods=["POST"])
@login_required
@req_with_copr
def copr_new_build_rebuild(copr, build_id):
    view='coprs_ns.copr_new_build'
    url_on_success = helpers.copr_url("coprs_ns.copr_builds", copr)
    return process_rebuild(copr, build_id, view=view, url_on_success=url_on_success)


def process_rebuild(copr, build_id, view, url_on_success):
    def factory(**build_options):
        source_build = ComplexLogic.get_build_safe(build_id)
        BuildsLogic.create_new_from_other_build(
            flask.g.user, copr, source_build,
            chroot_names=form.selected_chroots,
            **build_options
        )
    form = forms.BuildFormRebuildFactory.create_form_cls(copr.active_chroots)()
    return process_new_build(copr, form, factory, render_add_build, view, url_on_success)


################################ Repeat ################################

@coprs_ns.route("/<username>/<coprname>/repeat_build/<int:build_id>/", methods=["GET", "POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/repeat_build/<int:build_id>/", methods=["GET", "POST"])
@login_required
@req_with_copr
def copr_repeat_build(copr, build_id):
    return process_copr_repeat_build(build_id, copr)


def process_copr_repeat_build(build_id, copr):
    build = ComplexLogic.get_build_safe(build_id)
    if not flask.g.user.can_build_in(build.copr):
        flask.flash("You are not allowed to repeat this build.")

    if build.source_type == helpers.BuildSourceEnum('upload'):
        # If the original build's source is 'upload', we will show only the
        # original build's chroots and skip import.
        available_chroots = build.chroots

    else:
        # For all other sources, we will show all chroots enabled in the project
        # and proceed with import.
        available_chroots = copr.active_chroots

    form = forms.BuildFormRebuildFactory.create_form_cls(available_chroots)(
        build_id=build_id, enable_net=build.enable_net)

    # remove all checkboxes by default
    for ch in available_chroots:
        field = getattr(form, ch.name)
        field.data = False
    chroot_to_build = request.args.get("chroot")
    app.logger.debug("got param chroot: {}".format(chroot_to_build))
    if chroot_to_build:
        # set single checkbox if chroot query arg was provided
        if hasattr(form, chroot_to_build):
            getattr(form, chroot_to_build).data = True
    else:
        build_chroot_names = set(ch.name for ch in build.chroots)
        build_failed_chroot_names = set(ch.name for ch in build.get_chroots_by_status([
            helpers.StatusEnum('failed'), helpers.StatusEnum('canceled'),
        ]))
        for ch in available_chroots:
            # check checkbox on all the chroots that have not been (successfully) built before
            if (ch.name not in build_chroot_names) or (ch.name in build_failed_chroot_names):
                getattr(form, ch.name).data = True
    return flask.render_template(
        "coprs/detail/add_build/rebuild.html",
        copr=copr, build=build, form=form)


################################ Cancel ################################

def process_cancel_build(build):
    try:
        builds_logic.BuildsLogic.cancel_build(flask.g.user, build)
    except InsufficientRightsException as e:
        flask.flash(str(e), "error")
    else:
        db.session.commit()
        flask.flash("Build {} has been canceled successfully.".format(build.id))
    return flask.redirect(helpers.url_for_copr_builds(build.copr))


@coprs_ns.route("/<username>/<coprname>/cancel_build/<int:build_id>/", methods=["POST"])
@coprs_ns.route("/g/<group_name>/<coprname>/cancel_build/<int:build_id>/", methods=["POST"])
@login_required
@req_with_copr
def copr_cancel_build(copr, build_id):
    # only the user who ran the build can cancel it
    build = ComplexLogic.get_build_safe(build_id)
    return process_cancel_build(build)


################################ Delete ################################

@coprs_ns.route("/<username>/<coprname>/delete_build/<int:build_id>/",
                methods=["POST"])
@login_required
def copr_delete_build(username, coprname, build_id):
    build = ComplexLogic.get_build_safe(build_id)

    try:
        builds_logic.BuildsLogic.delete_build(flask.g.user, build)
    except (InsufficientRightsException, ActionInProgressException) as e:
        flask.flash(str(e), "error")
    else:
        db.session.commit()
        flask.flash("Build has been deleted successfully.")

    return flask.redirect(helpers.url_for_copr_builds(build.copr))
