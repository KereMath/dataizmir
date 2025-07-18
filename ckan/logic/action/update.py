# encoding: utf-8

'''API functions for updating existing data in CKAN.'''

import logging
import datetime
import time
import json

from ckan.common import config
import ckan.common as converters
import six
from six import text_type
import ckan.lib.helpers as h
import ckan.plugins as plugins
import ckan.logic as logic
import ckan.logic.schema as schema_
import ckan.lib.dictization as dictization
import ckan.lib.dictization.model_dictize as model_dictize
import ckan.lib.dictization.model_save as model_save
import ckan.lib.navl.dictization_functions as dfunc
import ckan.lib.navl.validators as validators
import ckan.lib.plugins as lib_plugins
import ckan.lib.email_notifications as email_notifications
import ckan.lib.search as search
import ckan.lib.uploader as uploader
import ckan.lib.datapreview
import ckan.lib.app_globals as app_globals
from ckan.model.theme import ThemeCategory, DatasetThemeAssignment
from ckan.model import meta, Session

from ckan.common import _, request

log = logging.getLogger(__name__)

# Define some shortcuts
# Ensure they are module-private so that they don't get loaded as available
# actions in the action API.
_validate = dfunc.validate
_get_action = logic.get_action
_check_access = logic.check_access
NotFound = logic.NotFound
ValidationError = logic.ValidationError
_get_or_bust = logic.get_or_bust


def resource_update(context, data_dict):
    '''Update a resource.

    To update a resource you must be authorized to update the dataset that the
    resource belongs to.

    .. note:: Update methods may delete parameters not explicitly provided in the
        data_dict. If you want to edit only a specific attribute use `resource_patch`
        instead.

    For further parameters see
    :py:func:`~ckan.logic.action.create.resource_create`.

    :param id: the id of the resource to update
    :type id: string

    :returns: the updated resource
    :rtype: string

    '''
    model = context['model']
    id = _get_or_bust(data_dict, "id")

    if not data_dict.get('url'):
        data_dict['url'] = ''

    resource = model.Resource.get(id)
    if not resource:
        log.debug('Could not find resource %s', id)
        raise NotFound(_('Resource was not found.'))

    context["resource"] = resource
    old_resource_format = resource.format

    _check_access('resource_update', context, data_dict)
    del context["resource"]

    package_id = resource.package.id
    package_show_context = dict(context, for_update=True, return_type='dict')
    pkg_dict = _get_action('package_show')(package_show_context,
        {'id': package_id})

    for n, p in enumerate(pkg_dict['resources']):
        if p['id'] == id:
            break
    else:
        log.error('Could not find resource %s after all', id)
        raise NotFound(_('Resource was not found.'))

    # Persist the datastore_active extra if already present and not provided
    if ('datastore_active' in resource.extras and
            'datastore_active' not in data_dict):
        data_dict['datastore_active'] = resource.extras['datastore_active']

    for plugin in plugins.PluginImplementations(plugins.IResourceController):
        plugin.before_update(context, pkg_dict['resources'][n], data_dict)

    pkg_dict['resources'][n] = data_dict

    try:
        context['use_cache'] = False
        updated_pkg_dict = _get_action('package_update')(context, pkg_dict)
    except ValidationError as e:
        try:
            raise ValidationError(e.error_dict['resources'][n])
        except (KeyError, IndexError):
            raise ValidationError(e.error_dict)

    resource = _get_action('resource_show')(context, {'id': id})

    if old_resource_format != resource['format']:
        _get_action('resource_create_default_resource_views')(
            {'model': context['model'], 'user': context['user'],
             'ignore_auth': True},
            {'package': updated_pkg_dict,
             'resource': resource})

    for plugin in plugins.PluginImplementations(plugins.IResourceController):
        plugin.after_update(context, resource)

    return resource


def resource_view_update(context, data_dict):
    '''Update a resource view.

    To update a resource_view you must be authorized to update the resource
    that the resource_view belongs to.

    For further parameters see ``resource_view_create()``.

    :param id: the id of the resource_view to update
    :type id: string

    :returns: the updated resource_view
    :rtype: string

    '''
    model = context['model']
    id = _get_or_bust(data_dict, "id")

    resource_view = model.ResourceView.get(id)
    if not resource_view:
        raise NotFound

    view_plugin = ckan.lib.datapreview.get_view_plugin(resource_view.view_type)
    schema = (context.get('schema') or
              schema_.default_update_resource_view_schema(view_plugin))
    plugin_schema = view_plugin.info().get('schema', {})
    schema.update(plugin_schema)

    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    context['resource_view'] = resource_view
    context['resource'] = model.Resource.get(resource_view.resource_id)

    _check_access('resource_view_update', context, data_dict)

    if context.get('preview'):
        return data

    resource_view = model_save.resource_view_dict_save(data, context)
    if not context.get('defer_commit'):
        model.repo.commit()
    return model_dictize.resource_view_dictize(resource_view, context)

def resource_view_reorder(context, data_dict):
    '''Reorder resource views.

    :param id: the id of the resource
    :type id: string
    :param order: the list of id of the resource to update the order of the views
    :type order: list of strings

    :returns: the updated order of the view
    :rtype: dictionary
    '''
    model = context['model']
    id, order = _get_or_bust(data_dict, ["id", "order"])
    if not isinstance(order, list):
        raise ValidationError({"order": "Must supply order as a list"})
    if len(order) != len(set(order)):
        raise ValidationError({"order": "No duplicates allowed in order"})
    resource = model.Resource.get(id)
    context['resource'] = resource

    _check_access('resource_view_reorder', context, data_dict)

    q = model.Session.query(model.ResourceView.id).filter_by(resource_id=id)
    existing_views = [res[0] for res in
                      q.order_by(model.ResourceView.order).all()]
    ordered_views = []
    for view in order:
        try:
            existing_views.remove(view)
            ordered_views.append(view)
        except ValueError:
            raise ValidationError(
                {"order": "View {view} does not exist".format(view=view)}
            )
    new_order = ordered_views + existing_views

    for num, view in enumerate(new_order):
        model.Session.query(model.ResourceView).\
            filter_by(id=view).update({"order": num + 1})
    model.Session.commit()
    return {'id': id, 'order': new_order}


def package_update(context, data_dict):
    '''Update a dataset (package).

    You must be authorized to edit the dataset and the groups that it belongs
    to.

    .. note:: Update methods may delete parameters not explicitly provided in the
        data_dict. If you want to edit only a specific attribute use `package_patch`
        instead.

    It is recommended to call
    :py:func:`ckan.logic.action.get.package_show`, make the desired changes to
    the result, and then call ``package_update()`` with it.

    Plugins may change the parameters of this function depending on the value
    of the dataset's ``type`` attribute, see the
    :py:class:`~ckan.plugins.interfaces.IDatasetForm` plugin interface.

    For further parameters see
    :py:func:`~ckan.logic.action.create.package_create`.

    :param id: the name or id of the dataset to update
    :type id: string

    :returns: the updated dataset (if ``'return_id_only'`` is ``False`` in
              the context, which is the default. Otherwise returns just the
              dataset id)
    :rtype: dictionary

    '''
    model = context['model']
    session = context['session']
    name_or_id = data_dict.get('id') or data_dict.get('name')
    if name_or_id is None:
        raise ValidationError({'id': _('Missing value')})

    pkg = model.Package.get(name_or_id)
    if pkg is None:
        raise NotFound(_('Package was not found.'))
    context["package"] = pkg

    # immutable fields
    data_dict["id"] = pkg.id
    data_dict['type'] = pkg.type

    _check_access('package_update', context, data_dict)

    user = context['user']
    # get the schema
    package_plugin = lib_plugins.lookup_package_plugin(pkg.type)
    if 'schema' in context:
        schema = context['schema']
    else:
        schema = package_plugin.update_package_schema()

    if 'api_version' not in context:
        # check_data_dict() is deprecated. If the package_plugin has a
        # check_data_dict() we'll call it, if it doesn't have the method we'll
        # do nothing.
        check_data_dict = getattr(package_plugin, 'check_data_dict', None)
        if check_data_dict:
            try:
                package_plugin.check_data_dict(data_dict, schema)
            except TypeError:
                # Old plugins do not support passing the schema so we need
                # to ensure they still work.
                package_plugin.check_data_dict(data_dict)

    resource_uploads = []
    for resource in data_dict.get('resources', []):
        # file uploads/clearing
        upload = uploader.get_resource_uploader(resource)

        if 'mimetype' not in resource:
            if hasattr(upload, 'mimetype'):
                resource['mimetype'] = upload.mimetype

        if 'url_type' in resource:
            if hasattr(upload, 'filesize'):
                resource['size'] = upload.filesize

        resource_uploads.append(upload)

    data, errors = lib_plugins.plugin_validate(
        package_plugin, context, data_dict, schema, 'package_update')
    log.debug('package_update validate_errs=%r user=%s package=%s data=%r',
              errors, context.get('user'),
              context.get('package').name if context.get('package') else '',
              data)

    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    #avoid revisioning by updating directly
    model.Session.query(model.Package).filter_by(id=pkg.id).update(
        {"metadata_modified": datetime.datetime.utcnow()})
    model.Session.refresh(pkg)

    pkg = model_save.package_dict_save(data, context)

    context_org_update = context.copy()
    context_org_update['ignore_auth'] = True
    context_org_update['defer_commit'] = True
    _get_action('package_owner_org_update')(context_org_update,
                                            {'id': pkg.id,
                                             'organization_id': pkg.owner_org})

    # Needed to let extensions know the new resources ids
    model.Session.flush()
    for index, (resource, upload) in enumerate(
            zip(data.get('resources', []), resource_uploads)):
        resource['id'] = pkg.resources[index].id

        upload.upload(resource['id'], uploader.get_max_resource_size())

    for item in plugins.PluginImplementations(plugins.IPackageController):
        item.edit(pkg)

        item.after_update(context, data)

    # Create activity
    if not pkg.private:
        user_obj = model.User.by_name(user)
        if user_obj:
            user_id = user_obj.id
        else:
            user_id = 'not logged in'

        activity = pkg.activity_stream_item('changed', user_id)
        session.add(activity)

    if not context.get('defer_commit'):
        model.repo.commit()

    log.debug('Updated object %s' % pkg.name)

    return_id_only = context.get('return_id_only', False)

    # Make sure that a user provided schema is not used on package_show
    context.pop('schema', None)

    # we could update the dataset so we should still be able to read it.
    context['ignore_auth'] = True
    output = data_dict['id'] if return_id_only \
            else _get_action('package_show')(context, {'id': data_dict['id']})

    return output


def package_revise(context, data_dict):
    '''Revise a dataset (package) selectively with match, filter and
    update parameters.

    You must be authorized to edit the dataset and the groups that it belongs
    to.

    :param match: a dict containing "id" or "name" values of the dataset
                  to update, all values provided must match the current
                  dataset values or a ValidationError will be raised. e.g.
                  ``{"name": "my-data", "resources": {["name": "big.csv"]}}``
                  would abort if the my-data dataset's first resource name
                  is not "big.csv".
    :type match: dict
    :param filter: a list of string patterns of fields to remove from the
                   current dataset. e.g. ``"-resources__1"`` would remove the
                   second resource, ``"+title, +resources, -*"`` would
                   remove all fields at the dataset level except title and
                   all resources (default: ``[]``)
    :type filter: comma-separated string patterns or list of string patterns
    :param update: a dict with values to update/create after filtering
                   e.g. ``{"resources": [{"description": "file here"}]}`` would
                   update the description for the first resource
    :type update: dict
    :param include: a list of string pattern of fields to include in response
                    e.g. ``"-*"`` to return nothing (default: ``[]`` all
                    fields returned)
    :type include: comma-separated-string patterns or list of string patterns

    ``match`` and ``update`` parameters may also be passed as "flattened keys", using
    either the item numeric index or its unique id (with a minimum of 5 characters), e.g.
    ``update__resource__1f9ab__description="guidebook"`` would set the
    description of the resource with id starting with "1f9ab" to "guidebook", and
    ``update__resource__-1__description="guidebook"`` would do the same
    on the last resource in the dataset.

    The ``extend`` suffix can also be used on the update parameter to add
    a new item to a list, e.g. ``update__resources__extend=[{"name": "new resource", "url": "https://example.com"}]``
    will add a new resource to the dataset with the provided ``name`` and ``url``.

    Usage examples:

    * Change description in dataset, checking for old description::

        match={"notes": "old notes", "name": "xyz"}
        update={"notes": "new notes"}

    * Identical to above, but using flattened keys::

        match__name="xyz"
        match__notes="old notes"
        update__notes="new notes"

    * Replace all fields at dataset level only, keep resources (note: dataset id
      and type fields can't be deleted) ::

        match={"id": "1234abc-1420-cbad-1922"}
        filter=["+resources", "-*"]
        update={"name": "fresh-start", "title": "Fresh Start"}

    * Add a new resource (__extend on flattened key)::

        match={"id": "abc0123-1420-cbad-1922"}
        update__resources__extend=[{"name": "new resource", "url": "http://example.com"}]

    * Update a resource by its index::

        match={"name": "my-data"}
        update__resources__0={"name": "new name, first resource"}

    * Update a resource by its id (provide at least 5 characters)::

        match={"name": "their-data"}
        update__resources__19cfad={"description": "right one for sure"}

    * Replace all fields of a resource::

        match={"id": "34a12bc-1420-cbad-1922"}
        filter=["+resources__1492a__id", "-resources__1492a__*"]
        update__resources__1492a={"name": "edits here", "url": "http://example.com"}


    :returns: a dict containing 'package':the updated dataset with fields filtered by include parameter
    :rtype: dictionary

    '''
    model = context['model']

    schema = schema_.package_revise_schema()
    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    name_or_id = (
        data['match__'].get('id') or
        data.get('match', {}).get('id') or
        data['match__'].get('name') or
        data.get('match', {}).get('name'))
    if name_or_id is None:
        raise ValidationError({'match__id': _('Missing value')})

    package_show_context = dict(
        context,
        return_type='dict',
        for_update=True)
    orig = _get_action('package_show')(
        package_show_context,
        {'id': name_or_id})

    pkg = package_show_context['package']  # side-effect of package_show

    unmatched = []
    if 'match' in data:
        unmatched.extend(dfunc.check_dict(orig, data['match']))

    for k, v in sorted(data['match__'].items()):
        unmatched.extend(dfunc.check_string_key(orig, k, v))

    if unmatched:
        model.Session.rollback()
        raise ValidationError([{'match': [
            '__'.join(str(p) for p in unm)
            for unm in unmatched
        ]}])

    if 'filter' in data:
        orig_id = orig['id']
        dfunc.filter_glob_match(orig, data['filter'])
        orig['id'] = orig_id

    if 'update' in data:
        try:
            dfunc.update_merge_dict(orig, data['update'])
        except dfunc.DataError as de:
            model.Session.rollback()
            raise ValidationError([{'update': [de.error]}])

    # update __extend keys before __#__* so that files may be
    # attached to newly added resources in the same call
    try:
        for k, v in sorted(
                data['update__'].items(),
                key=lambda s: s[0][-6] if s[0].endswith('extend') else s[0]):
            dfunc.update_merge_string_key(orig, k, v)
    except dfunc.DataError as de:
        model.Session.rollback()
        raise ValidationError([{k: [de.error]}])

    _check_access('package_revise', context, {"update": orig})

    # future-proof return dict by putting package data under
    # "package". We will want to return activity info
    # on update or "nothing changed" status once possible
    rval = {
        'package': _get_action('package_update')(
            dict(context, package=pkg),
            orig)}
    if 'include' in data_dict:
        dfunc.filter_glob_match(rval, data_dict['include'])
    return rval


def package_resource_reorder(context, data_dict):
    '''Reorder resources against datasets.  If only partial resource ids are
    supplied then these are assumed to be first and the other resources will
    stay in their original order

    :param id: the id or name of the package to update
    :type id: string
    :param order: a list of resource ids in the order needed
    :type order: list
    '''

    id = _get_or_bust(data_dict, "id")
    order = _get_or_bust(data_dict, "order")
    if not isinstance(order, list):
        raise ValidationError({'order': 'Must be a list of resource'})

    if len(set(order)) != len(order):
        raise ValidationError({'order': 'Must supply unique resource_ids'})

    package_show_context = dict(context, for_update=True, return_type='dict')
    package_dict = _get_action('package_show')(package_show_context, {'id': id})
    existing_resources = package_dict.get('resources', [])
    ordered_resources = []

    for resource_id in order:
        for i in range(0, len(existing_resources)):
            if existing_resources[i]['id'] == resource_id:
                resource = existing_resources.pop(i)
                ordered_resources.append(resource)
                break
        else:
            raise ValidationError(
                {'order':
                 'resource_id {id} can not be found'.format(id=resource_id)}
            )

    new_resources = ordered_resources + existing_resources
    package_dict['resources'] = new_resources

    _check_access('package_resource_reorder', context, package_dict)
    _get_action('package_update')(context, package_dict)

    return {'id': id, 'order': [resource['id'] for resource in new_resources]}


def _update_package_relationship(relationship, comment, context):
    model = context['model']
    api = context.get('api_version')
    ref_package_by = 'id' if api == 2 else 'name'
    is_changed = relationship.comment != comment
    if is_changed:
        relationship.comment = comment
        if not context.get('defer_commit'):
            model.repo.commit_and_remove()
    rel_dict = relationship.as_dict(package=relationship.subject,
                                    ref_package_by=ref_package_by)
    return rel_dict


def package_relationship_update(context, data_dict):
    '''Update a relationship between two datasets (packages).

    The subject, object and type parameters are required to identify the
    relationship. Only the comment can be updated.

    You must be authorized to edit both the subject and the object datasets.

    :param subject: the name or id of the dataset that is the subject of the
        relationship
    :type subject: string
    :param object: the name or id of the dataset that is the object of the
        relationship
    :type object: string
    :param type: the type of the relationship, one of ``'depends_on'``,
        ``'dependency_of'``, ``'derives_from'``, ``'has_derivation'``,
        ``'links_to'``, ``'linked_from'``, ``'child_of'`` or ``'parent_of'``
    :type type: string
    :param comment: a comment about the relationship (optional)
    :type comment: string

    :returns: the updated relationship
    :rtype: dictionary

    '''
    model = context['model']
    schema = context.get('schema') \
        or schema_.default_update_relationship_schema()

    id, id2, rel = _get_or_bust(data_dict, ['subject', 'object', 'type'])

    pkg1 = model.Package.get(id)
    pkg2 = model.Package.get(id2)
    if not pkg1:
        raise NotFound('Subject package %r was not found.' % id)
    if not pkg2:
        return NotFound('Object package %r was not found.' % id2)

    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    _check_access('package_relationship_update', context, data_dict)

    existing_rels = pkg1.get_relationships_with(pkg2, rel)
    if not existing_rels:
        raise NotFound('This relationship between the packages was not found.')
    entity = existing_rels[0]
    comment = data_dict.get('comment', u'')
    context['relationship'] = entity
    return _update_package_relationship(entity, comment, context)


def _group_or_org_update(context, data_dict, is_org=False):
    model = context['model']
    user = context['user']
    session = context['session']
    id = _get_or_bust(data_dict, 'id')

    group = model.Group.get(id)
    context["group"] = group
    if group is None:
        raise NotFound('Group was not found.')

    data_dict['type'] = group.type

    # get the schema
    group_plugin = lib_plugins.lookup_group_plugin(group.type)
    try:
        schema = group_plugin.form_to_db_schema_options({'type': 'update',
                                               'api': 'api_version' in context,
                                               'context': context})
    except AttributeError:
        schema = group_plugin.form_to_db_schema()

    upload = uploader.get_uploader('group')
    upload.update_data_dict(data_dict, 'image_url',
                            'image_upload', 'clear_upload')

    if is_org:
        _check_access('organization_update', context, data_dict)
    else:
        _check_access('group_update', context, data_dict)

    if 'api_version' not in context:
        # old plugins do not support passing the schema so we need
        # to ensure they still work
        try:
            group_plugin.check_data_dict(data_dict, schema)
        except TypeError:
            group_plugin.check_data_dict(data_dict)

    data, errors = lib_plugins.plugin_validate(
        group_plugin, context, data_dict, schema,
        'organization_update' if is_org else 'group_update')
    log.debug('group_update validate_errs=%r user=%s group=%s data_dict=%r',
              errors, context.get('user'),
              context.get('group').name if context.get('group') else '',
              data_dict)

    if errors:
        session.rollback()
        raise ValidationError(errors)

    contains_packages = 'packages' in data_dict

    group = model_save.group_dict_save(
        data, context,
        prevent_packages_update=is_org or not contains_packages
    )

    if is_org:
        plugin_type = plugins.IOrganizationController
    else:
        plugin_type = plugins.IGroupController

    for item in plugins.PluginImplementations(plugin_type):
        item.edit(group)

    if is_org:
        activity_type = 'changed organization'
    else:
        activity_type = 'changed group'

    activity_dict = {
            'user_id': model.User.by_name(six.ensure_text(user)).id,
            'object_id': group.id,
            'activity_type': activity_type,
            }
    # Handle 'deleted' groups.
    # When the user marks a group as deleted this comes through here as
    # a 'changed' group activity. We detect this and change it to a 'deleted'
    # activity.
    if group.state == u'deleted':
        if session.query(ckan.model.Activity).filter_by(
                object_id=group.id, activity_type='deleted').all():
            # A 'deleted group' activity for this group has already been
            # emitted.
            # FIXME: What if the group was deleted and then activated again?
            activity_dict = None
        else:
            # We will emit a 'deleted group' activity.
            activity_dict['activity_type'] = \
                'deleted organization' if is_org else 'deleted group'
    if activity_dict is not None:
        activity_dict['data'] = {
                'group': dictization.table_dictize(group, context)
                }
        activity_create_context = {
            'model': model,
            'user': user,
            'defer_commit': True,
            'ignore_auth': True,
            'session': session
        }
        _get_action('activity_create')(activity_create_context, activity_dict)
        # TODO: Also create an activity detail recording what exactly changed
        # in the group.

    upload.upload(uploader.get_max_image_size())

    if not context.get('defer_commit'):
        model.repo.commit()

    return model_dictize.group_dictize(group, context)


def group_update(context, data_dict):
    '''Update a group.

    You must be authorized to edit the group.

    .. note:: Update methods may delete parameters not explicitly provided in the
        data_dict. If you want to edit only a specific attribute use `group_patch`
        instead.

    Plugins may change the parameters of this function depending on the value
    of the group's ``type`` attribute, see the
    :py:class:`~ckan.plugins.interfaces.IGroupForm` plugin interface.

    For further parameters see
    :py:func:`~ckan.logic.action.create.group_create`.

    :param id: the name or id of the group to update
    :type id: string

    :returns: the updated group
    :rtype: dictionary

    '''
    # Callers that set context['allow_partial_update'] = True can choose to not
    # specify particular keys and they will be left at their existing
    # values. This includes: packages, users, groups, tags, extras
    return _group_or_org_update(context, data_dict)

def organization_update(context, data_dict):
    '''Update a organization.

    You must be authorized to edit the organization.

    .. note:: Update methods may delete parameters not explicitly provided in the
        data_dict. If you want to edit only a specific attribute use `organization_patch`
        instead.

    For further parameters see
    :py:func:`~ckan.logic.action.create.organization_create`.

    :param id: the name or id of the organization to update
    :type id: string
    :param packages: ignored. use
        :py:func:`~ckan.logic.action.update.package_owner_org_update`
        to change package ownership

    :returns: the updated organization
    :rtype: dictionary

    '''
    # Callers that set context['allow_partial_update'] = True can choose to not
    # specify particular keys and they will be left at their existing
    # values. This includes: users, groups, tags, extras
    return _group_or_org_update(context, data_dict, is_org=True)

def user_update(context, data_dict):
    '''Update a user account.

    Normal users can only update their own user accounts. Sysadmins can update
    any user account. Can not modify exisiting user's name.

    .. note:: Update methods may delete parameters not explicitly provided in the
        data_dict. If you want to edit only a specific attribute use `user_patch`
        instead.

    For further parameters see
    :py:func:`~ckan.logic.action.create.user_create`.

    :param id: the name or id of the user to update
    :type id: string

    :returns: the updated user account
    :rtype: dictionary

    '''
    model = context['model']
    user = author = context['user']
    session = context['session']
    schema = context.get('schema') or schema_.default_update_user_schema()
    id = _get_or_bust(data_dict, 'id')

    user_obj = model.User.get(id)
    context['user_obj'] = user_obj
    if user_obj is None:
        raise NotFound('User was not found.')

    _check_access('user_update', context, data_dict)

    upload = uploader.get_uploader('user')
    upload.update_data_dict(data_dict, 'image_url',
                            'image_upload', 'clear_upload')

    data, errors = _validate(data_dict, schema, context)
    if errors:
        session.rollback()
        raise ValidationError(errors)

    # user schema prevents non-sysadmins from providing password_hash
    if 'password_hash' in data:
        data['_password'] = data.pop('password_hash')

    user = model_save.user_dict_save(data, context)

    activity_dict = {
            'user_id': user.id,
            'object_id': user.id,
            'activity_type': 'changed user',
            }
    activity_create_context = {
        'model': model,
        'user': author,
        'defer_commit': True,
        'ignore_auth': True,
        'session': session
    }
    _get_action('activity_create')(activity_create_context, activity_dict)
    # TODO: Also create an activity detail recording what exactly changed in
    # the user.

    upload.upload(uploader.get_max_image_size())

    if not context.get('defer_commit'):
        model.repo.commit()

    author_obj = model.User.get(context.get('user'))
    include_plugin_extras = False
    if author_obj:
        include_plugin_extras = author_obj.sysadmin and 'plugin_extras' in data
    user_dict = model_dictize.user_dictize(
        user, context, include_plugin_extras=include_plugin_extras)

    return user_dict


def user_generate_apikey(context, data_dict):
    '''Cycle a user's API key

    :param id: the name or id of the user whose key needs to be updated
    :type id: string

    :returns: the updated user
    :rtype: dictionary
    '''
    model = context['model']
    schema = context.get('schema') or schema_.default_generate_apikey_user_schema()
    context['schema'] = schema
    # check if user id in data_dict
    id = _get_or_bust(data_dict, 'id')

    # check if user exists
    user_obj = model.User.get(id)
    context['user_obj'] = user_obj
    if user_obj is None:
        raise NotFound('User was not found.')

    # check permission
    _check_access('user_generate_apikey', context, data_dict)

    # change key
    old_data = _get_action('user_show')(context, data_dict)
    old_data['apikey'] = model.types.make_uuid()
    data_dict = old_data
    return _get_action('user_update')(context, data_dict)


def task_status_update(context, data_dict):
    '''Update a task status.

    :param id: the id of the task status to update
    :type id: string
    :param entity_id:
    :type entity_id: string
    :param entity_type:
    :type entity_type: string
    :param task_type:
    :type task_type: string
    :param key:
    :type key: string
    :param value: (optional)
    :type value:
    :param state: (optional)
    :type state:
    :param last_updated: (optional)
    :type last_updated:
    :param error: (optional)
    :type error:

    :returns: the updated task status
    :rtype: dictionary

    '''
    model = context['model']
    session = context['session']

    id = data_dict.get("id")
    schema = context.get('schema') or schema_.default_task_status_schema()

    if id:
        task_status = model.TaskStatus.get(id)
        context["task_status"] = task_status

        if task_status is None:
            raise NotFound(_('TaskStatus was not found.'))

    _check_access('task_status_update', context, data_dict)

    data, errors = _validate(data_dict, schema, context)
    if errors:
        session.rollback()
        raise ValidationError(errors)

    task_status = model_save.task_status_dict_save(data, context)

    session.commit()
    session.close()
    return model_dictize.task_status_dictize(task_status, context)

def task_status_update_many(context, data_dict):
    '''Update many task statuses at once.

    :param data: the task_status dictionaries to update, for the format of task
        status dictionaries see
        :py:func:`~task_status_update`
    :type data: list of dictionaries

    :returns: the updated task statuses
    :rtype: list of dictionaries

    '''
    results = []
    model = context['model']
    deferred = context.get('defer_commit')
    context['defer_commit'] = True
    for data in data_dict['data']:
        results.append(_get_action('task_status_update')(context, data))
    if not deferred:
        context.pop('defer_commit')
    if not context.get('defer_commit'):
        model.Session.commit()
    return {'results': results}

def term_translation_update(context, data_dict):
    '''Create or update a term translation.

    You must be a sysadmin to create or update term translations.

    :param term: the term to be translated, in the original language, e.g.
        ``'romantic novel'``
    :type term: string
    :param term_translation: the translation of the term, e.g.
        ``'Liebesroman'``
    :type term_translation: string
    :param lang_code: the language code of the translation, e.g. ``'de'``
    :type lang_code: string

    :returns: the newly created or updated term translation
    :rtype: dictionary

    '''
    model = context['model']

    _check_access('term_translation_update', context, data_dict)

    schema = {'term': [validators.not_empty, text_type],
              'term_translation': [validators.not_empty, text_type],
              'lang_code': [validators.not_empty, text_type]}

    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    trans_table = model.term_translation_table

    update = trans_table.update()
    update = update.where(trans_table.c.term == data['term'])
    update = update.where(trans_table.c.lang_code == data['lang_code'])
    update = update.values(term_translation = data['term_translation'])

    conn = model.Session.connection()
    result = conn.execute(update)

    # insert if not updated
    if not result.rowcount:
        conn.execute(trans_table.insert().values(**data))

    if not context.get('defer_commit'):
        model.Session.commit()

    return data

def term_translation_update_many(context, data_dict):
    '''Create or update many term translations at once.

    :param data: the term translation dictionaries to create or update,
        for the format of term translation dictionaries see
        :py:func:`~term_translation_update`
    :type data: list of dictionaries

    :returns: a dictionary with key ``'success'`` whose value is a string
        stating how many term translations were updated
    :rtype: string

    '''
    model = context['model']

    if not (data_dict.get('data') and isinstance(data_dict.get('data'), list)):
        raise ValidationError(
            {'error': 'term_translation_update_many needs to have a '
                      'list of dicts in field data'}
        )

    context['defer_commit'] = True

    action = _get_action('term_translation_update')
    for num, row in enumerate(data_dict['data']):
        action(context, row)

    model.Session.commit()

    return {'success': '%s rows updated' % (num + 1)}


def vocabulary_update(context, data_dict):
    '''Update a tag vocabulary.

    You must be a sysadmin to update vocabularies.

    For further parameters see
    :py:func:`~ckan.logic.action.create.vocabulary_create`.

    :param id: the id of the vocabulary to update
    :type id: string

    :returns: the updated vocabulary
    :rtype: dictionary

    '''
    model = context['model']

    vocab_id = data_dict.get('id')
    if not vocab_id:
        raise ValidationError({'id': _('id not in data')})

    vocab = model.vocabulary.Vocabulary.get(vocab_id)
    if vocab is None:
        raise NotFound(_('Could not find vocabulary "%s"') % vocab_id)

    data_dict['id'] = vocab.id
    if 'name' in data_dict:
        if data_dict['name'] == vocab.name:
            del data_dict['name']

    _check_access('vocabulary_update', context, data_dict)

    schema = context.get('schema') or schema_.default_update_vocabulary_schema()
    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    updated_vocab = model_save.vocabulary_dict_update(data, context)

    if not context.get('defer_commit'):
        model.repo.commit()

    return model_dictize.vocabulary_dictize(updated_vocab, context)


def dashboard_mark_activities_old(context, data_dict):
    '''Mark all the authorized user's new dashboard activities as old.

    This will reset
    :py:func:`~ckan.logic.action.get.dashboard_new_activities_count` to 0.

    '''
    _check_access('dashboard_mark_activities_old', context,
            data_dict)
    model = context['model']
    user_id = model.User.get(context['user']).id
    model.Dashboard.get(user_id).activity_stream_last_viewed = (
            datetime.datetime.utcnow())
    if not context.get('defer_commit'):
        model.repo.commit()


@logic.auth_audit_exempt
def send_email_notifications(context, data_dict):
    '''Send any pending activity stream notification emails to users.

    You must provide a sysadmin's API key in the Authorization header of the
    request, or call this action from the command-line via a `paster post ...`
    command.

    '''
    # If paste.command_request is True then this function has been called
    # by a `paster post ...` command not a real HTTP request, so skip the
    # authorization.
    if not request.environ.get('paste.command_request'):
        _check_access('send_email_notifications', context, data_dict)

    if not converters.asbool(
            config.get('ckan.activity_streams_email_notifications')):
        raise ValidationError('ckan.activity_streams_email_notifications'
                              ' is not enabled in config')

    email_notifications.get_and_send_notifications_for_all_users()


def package_owner_org_update(context, data_dict):
    '''Update the owning organization of a dataset

    :param id: the name or id of the dataset to update
    :type id: string

    :param organization_id: the name or id of the owning organization
    :type organization_id: string
    '''
    model = context['model']
    name_or_id = data_dict.get('id')
    organization_id = data_dict.get('organization_id')

    _check_access('package_owner_org_update', context, data_dict)

    pkg = model.Package.get(name_or_id)
    if pkg is None:
        raise NotFound(_('Package was not found.'))
    if organization_id:
        org = model.Group.get(organization_id)
        if org is None or not org.is_organization:
            raise NotFound(_('Organization was not found.'))

        # FIXME check we are in that org
        pkg.owner_org = org.id
    else:
        org = None
        pkg.owner_org = None

    members = model.Session.query(model.Member) \
        .filter(model.Member.table_id == pkg.id) \
        .filter(model.Member.capacity == 'organization')

    need_update = True
    for member_obj in members:
        if org and member_obj.group_id == org.id:
            need_update = False
        else:
            member_obj.state = 'deleted'
            member_obj.save()

    # add the organization to member table
    if org and need_update:
        member_obj = model.Member(table_id=pkg.id,
                                  table_name='package',
                                  group=org,
                                  capacity='organization',
                                  group_id=org.id,
                                  state='active')
        model.Session.add(member_obj)

    if not context.get('defer_commit'):
        model.Session.commit()


def _bulk_update_dataset(context, data_dict, update_dict):
    ''' Bulk update shared code for organizations'''

    datasets = data_dict.get('datasets', [])
    org_id = data_dict.get('org_id')

    model = context['model']
    model.Session.query(model.package_table) \
        .filter(model.Package.id.in_(datasets)) \
        .filter(model.Package.owner_org == org_id) \
        .update(update_dict, synchronize_session=False)

    # Handle Activity Stream for Bulk Operations
    user = context['user']
    user_obj = model.User.by_name(user)
    if user_obj:
        user_id = user_obj.id
    else:
        user_id = 'not logged in'
    for dataset in datasets:
        entity = model.Package.get(dataset)
        activity = entity.activity_stream_item('changed', user_id)
        model.Session.add(activity)
    model.Session.commit()

    # solr update here
    psi = search.PackageSearchIndex()

    # update the solr index in batches
    BATCH_SIZE = 50

    def process_solr(q):
        # update the solr index for the query
        query = search.PackageSearchQuery()
        q = {
            'q': q,
            'fl': 'data_dict',
            'wt': 'json',
            'fq': 'site_id:"%s"' % config.get('ckan.site_id'),
            'rows': BATCH_SIZE
        }

        for result in query.run(q)['results']:
            data_dict = json.loads(result['data_dict'])
            if data_dict['owner_org'] == org_id:
                data_dict.update(update_dict)
                psi.index_package(data_dict, defer_commit=True)

    count = 0
    q = []
    for id in datasets:
        q.append('id:"%s"' % (id))
        count += 1
        if count % BATCH_SIZE == 0:
            process_solr(' OR '.join(q))
            q = []
    if len(q):
        process_solr(' OR '.join(q))
    # finally commit the changes
    psi.commit()


def bulk_update_private(context, data_dict):
    ''' Make a list of datasets private

    :param datasets: list of ids of the datasets to update
    :type datasets: list of strings

    :param org_id: id of the owning organization
    :type org_id: int
    '''

    _check_access('bulk_update_private', context, data_dict)
    _bulk_update_dataset(context, data_dict, {'private': True})

def bulk_update_public(context, data_dict):
    ''' Make a list of datasets public

    :param datasets: list of ids of the datasets to update
    :type datasets: list of strings

    :param org_id: id of the owning organization
    :type org_id: int
    '''

    _check_access('bulk_update_public', context, data_dict)
    _bulk_update_dataset(context, data_dict, {'private': False})

def bulk_update_delete(context, data_dict):
    ''' Make a list of datasets deleted

    :param datasets: list of ids of the datasets to update
    :type datasets: list of strings

    :param org_id: id of the owning organization
    :type org_id: int
    '''

    _check_access('bulk_update_delete', context, data_dict)
    _bulk_update_dataset(context, data_dict, {'state': 'deleted'})


def config_option_update(context, data_dict):
    '''

    .. versionadded:: 2.4

    Allows to modify some CKAN runtime-editable config options

    It takes arbitrary key, value pairs and checks the keys against the
    config options update schema. If some of the provided keys are not present
    in the schema a :py:class:`~ckan.plugins.logic.ValidationError` is raised.
    The values are then validated against the schema, and if validation is
    passed, for each key, value config option:

    * It is stored on the ``system_info`` database table
    * The Pylons ``config`` object is updated.
    * The ``app_globals`` (``g``) object is updated (this only happens for
      options explicitly defined in the ``app_globals`` module.

    The following lists a ``key`` parameter, but this should be replaced by
    whichever config options want to be updated, eg::

        get_action('config_option_update)({}, {
            'ckan.site_title': 'My Open Data site',
            'ckan.homepage_layout': 2,
        })

    :param key: a configuration option key (eg ``ckan.site_title``). It must
        be present on the ``update_configuration_schema``
    :type key: string

    :returns: a dictionary with the options set
    :rtype: dictionary

    .. note:: You can see all available runtime-editable configuration options
        calling
        the :py:func:`~ckan.logic.action.get.config_option_list` action

    .. note:: Extensions can modify which configuration options are
        runtime-editable.
        For details, check :doc:`/extensions/remote-config-update`.

    .. warning:: You should only add config options that you are comfortable
        they can be edited during runtime, such as ones you've added in your
        own extension, or have reviewed the use of in core CKAN.

    '''
    model = context['model']

    _check_access('config_option_update', context, data_dict)

    schema = schema_.update_configuration_schema()

    available_options = schema.keys()

    provided_options = data_dict.keys()

    unsupported_options = set(provided_options) - set(available_options)
    if unsupported_options:
        msg = 'Configuration option(s) \'{0}\' can not be updated'.format(
              ' '.join(list(unsupported_options)))

        raise ValidationError(msg, error_summary={'message': msg})

    upload = uploader.get_uploader('admin')
    upload.update_data_dict(data_dict, 'ckan.site_logo',
                            'logo_upload', 'clear_logo_upload')
    upload.upload(uploader.get_max_image_size())
    data, errors = _validate(data_dict, schema, context)
    if errors:
        model.Session.rollback()
        raise ValidationError(errors)

    for key, value in six.iteritems(data):

        # Set full Logo url
        if key == 'ckan.site_logo' and value and not value.startswith('http')\
                and not value.startswith('/'):
            image_path = 'uploads/admin/'

            value = h.url_for_static('{0}{1}'.format(image_path, value))

        # Save value in database
        model.set_system_info(key, value)

        # Update CKAN's `config` object
        config[key] = value

        # Only add it to the app_globals (`g`) object if explicitly defined
        # there
        globals_keys = app_globals.app_globals_from_config_details.keys()
        if key in globals_keys:
            app_globals.set_app_global(key, value)

    # Update the config update timestamp
    model.set_system_info('ckan.config_update', str(time.time()))

    log.info('Updated config options: {0}'.format(data))

    return data

# Theme UPDATE Actions

def theme_category_update(context, data_dict):
    """Kategori güncelle"""
    from ckan.model.theme import ThemeCategory
    from ckan.model import Session, User
    from ckan.logic import ValidationError, NotAuthorized, NotFound, _
    from ckan import logic # Ensure ckan.logic is imported for _check_access etc.

    # Yetkilendirme kontrolü
    user_obj = context.get('auth_user_obj')
    if not user_obj and context.get('user'):
        user_obj = Session.query(User).filter_by(name=context['user']).first()

    is_sysadmin = user_obj and user_obj.sysadmin
    is_theme_authorized_for_update = False

    slug = data_dict.get('slug')
    if not slug:
        raise ValidationError('slug parameter required')

    if is_sysadmin:
        is_theme_authorized_for_update = True
    elif user_obj:
        user_id = user_obj.id
        # Kullanıcının atanmış temalarını ve rollerini çek
        user_themes = logic.get_action('get_user_themes')(context, {'user_id': user_id})
        current_user_assignment = next((t for t in user_themes if t['theme_slug'] == slug), None)

        if current_user_assignment:
            assigned_role = current_user_assignment['role']
            if assigned_role in ['admin', 'editor']: # admin veya editor ise izin ver
                is_theme_authorized_for_update = True

    if not is_theme_authorized_for_update:
        log.warning(f"User {user_obj.name if user_obj else 'anonymous'} not authorized to update theme {slug}.")
        raise NotAuthorized(_('Bu temayı güncellemek için yetkiniz yok.'))

    try:
        session = Session()

        category = session.query(ThemeCategory).filter_by(slug=slug).first()
        if not category:
            log.error(f"Theme category with slug '{slug}' not found for update.")
            raise NotFound('Theme category not found')

        # Güncellenebilir alanlar
        if 'name' in data_dict:
            category.name = data_dict['name']
        if 'description' in data_dict:
            category.description = data_dict['description']
        if 'color' in data_dict:
            category.color = data_dict['color']
        if 'icon' in data_dict:
            category.icon = data_dict['icon']
        # >>> BURADAN BAŞLAYAN KISIM ÖNCEKİ VERSİYONLARDA EKSİKTİ VEYA HATALIYDI <<<
        if 'opacity' in data_dict:
            category.opacity = data_dict['opacity']
        # >>> BURAYA KADAR <<<

        # CRITICAL FIX: Ensure background_image is handled correctly
        # The 'background_image' will be present in data_dict if a new file was uploaded
        # OR if the 'clear_background_image' checkbox was used (in which case it's None)
        # OR if no image changes were made (in which case it's the old path).
        # We simply assign whatever is in data_dict for 'background_image'.
        if 'background_image' in data_dict:
            log.info(f"Action: Attempting to set background_image for {slug} to: '{data_dict['background_image']}'")
            category.background_image = data_dict['background_image']


        session.commit()
        # Loglama da opacity'yi gösterecek şekilde güncellendi
        log.info(f"Theme category '{slug}' successfully committed to database. Background_image after commit: '{category.background_image}', Opacity: '{category.opacity}'")

        return {
            'id': category.id,
            'slug': category.slug,
            'name': category.name,
            'description': category.description,
            'color': category.color,
            'icon': category.icon,
            'background_image': category.background_image,
            'opacity': category.opacity, # Bu satır EKSİKTİ!
            'created_at': category.created_at.isoformat() if category.created_at else None
        }
    except Exception as e:
        session.rollback()
        log.error(f"Error during theme_category_update for {slug}: {e}", exc_info=True)
        if isinstance(e, (ValidationError, NotFound, NotAuthorized)):
            raise
        raise ValidationError(f'Error updating category: {str(e)}')

def remove_dataset_theme(context, data_dict):
    """Dataset'ten tema kaldır"""
    from ckan.model.theme import DatasetThemeAssignment
    from ckan.model import Session, User
    from ckan.logic import ValidationError, NotAuthorized, NotFound, _check_access, _ # Added _check_access, _

    dataset_id = data_dict.get('dataset_id')

    if not dataset_id:
        raise ValidationError('dataset_id is required')

    # Yetkilendirme kontrolü
    user_obj = context.get('auth_user_obj')
    if not user_obj and context.get('user'):
        user_obj = Session.query(User).filter_by(name=context['user']).first()

    is_sysadmin = user_obj and user_obj.sysadmin
    is_theme_authorized_for_removal = False

    # Tema slug'ı doğrudan data_dict'te olmayabilir, mevcut atamadan bulmalıyız
    current_assignment = Session.query(DatasetThemeAssignment).filter_by(dataset_id=dataset_id).first()
    theme_slug = current_assignment.theme_slug if current_assignment else None

    if not theme_slug: # Tema ataması yoksa kaldıracak bir şey de yoktur
        return {'success': True, 'dataset_id': dataset_id, 'message': 'No theme assigned to this dataset to remove.'}


    if is_sysadmin:
        is_theme_authorized_for_removal = True
    elif user_obj:
        user_id = user_obj.id
        user_themes = logic.get_action('get_user_themes')(context, {'user_id': user_id})
        current_user_assignment = next((t for t in user_themes if t['theme_slug'] == theme_slug), None)
        if current_user_assignment:
            assigned_role = current_user_assignment['role']
            if assigned_role in ['admin', 'editor']: # Temanın admini veya editörü ise izin ver
                is_theme_authorized_for_removal = True

    # Kullanıcının veri setini güncelleme yetkisi olmalı
    is_package_update_authorized = False
    try:
        _check_access('package_update', context, {'id': dataset_id})
        is_package_update_authorized = True
    except NotAuthorized:
        pass

    # Hem tema hem de veri seti için yetki kontrolü
    if not (is_theme_authorized_for_removal and is_package_update_authorized):
        raise NotAuthorized(_('Bu temadan veri seti kaldırmak için yetkiniz yok veya veri setini düzenleme yetkiniz yok.'))

    try:
        session = Session()

        assignment = session.query(DatasetThemeAssignment).filter_by(dataset_id=dataset_id).first()
        if assignment:
            session.delete(assignment)
            session.commit()

        return {'success': True, 'dataset_id': dataset_id}
    except Exception as e:
        session.rollback()
        raise ValidationError(f'Error removing theme: {str(e)}')


def update_user_theme_role(context, data_dict):
    """Kullanıcının tema rolünü güncelle"""
    from ckan.model.theme import UserThemeAssignment
    from ckan.model import Session
    from ckan.logic import ValidationError, NotAuthorized, NotFound, _check_access, _ # Added _check_access, _

    _check_access('sysadmin', context, data_dict)

    user_id = data_dict.get('user_id')
    theme_slug = data_dict.get('theme_slug')
    new_role = data_dict.get('role')

    if not user_id or not theme_slug or not new_role:
        raise ValidationError('user_id, theme_slug and role are required')

    if new_role not in ['member', 'editor', 'admin']:
        raise ValidationError('role must be one of: member, editor, admin')

    try:
        session = Session()

        assignment = session.query(UserThemeAssignment).filter_by(
            user_id=user_id,
            theme_slug=theme_slug
        ).first()

        if not assignment:
            raise NotFound('User theme assignment not found')

        assignment.role = new_role
        assignment.assigned_by = context.get('user')
        session.commit()

        return {'success': True, 'role': new_role}
    except Exception as e:
        session.rollback()
        if isinstance(e, (ValidationError, NotFound)):
            raise
        raise ValidationError(f'Error updating user theme role: {str(e)}')

def remove_dataset_theme(context, data_dict):
    """Dataset'ten tema kaldır"""
    from ckan.model.theme import DatasetThemeAssignment
    from ckan.model import Session, User
    
    dataset_id = data_dict.get('dataset_id')
    
    if not dataset_id:
        raise ValidationError('dataset_id is required')

    # Yetkilendirme kontrolü
    user_obj = context.get('auth_user_obj')
    if not user_obj and context.get('user'):
        user_obj = Session.query(User).filter_by(name=context['user']).first()

    is_sysadmin = user_obj and user_obj.sysadmin
    is_theme_authorized_for_removal = False

    # Tema slug'ı doğrudan data_dict'te olmayabilir, mevcut atamadan bulmalıyız
    current_assignment = Session.query(DatasetThemeAssignment).filter_by(dataset_id=dataset_id).first()
    theme_slug = current_assignment.theme_slug if current_assignment else None

    if not theme_slug: # Tema ataması yoksa kaldıracak bir şey de yoktur
        return {'success': True, 'dataset_id': dataset_id, 'message': 'No theme assigned to this dataset to remove.'}


    if is_sysadmin:
        is_theme_authorized_for_removal = True
    elif user_obj:
        user_id = user_obj.id
        user_themes = logic.get_action('get_user_themes')(context, {'user_id': user_id})
        current_user_assignment = next((t for t in user_themes if t['theme_slug'] == theme_slug), None)
        if current_user_assignment:
            assigned_role = current_user_assignment['role']
            if assigned_role in ['admin', 'editor']: # Temanın admini veya editörü ise izin ver
                is_theme_authorized_for_removal = True
    
    # Kullanıcının veri setini güncelleme yetkisi olmalı
    is_package_update_authorized = False
    try:
        _check_access('package_update', context, {'id': dataset_id})
        is_package_update_authorized = True
    except NotAuthorized:
        pass

    # Hem tema hem de veri seti için yetki kontrolü
    if not (is_theme_authorized_for_removal and is_package_update_authorized):
        raise NotAuthorized(_('Bu temadan veri seti kaldırmak için yetkiniz yok veya veri setini düzenleme yetkiniz yok.'))
    
    try:
        session = Session()
        
        assignment = session.query(DatasetThemeAssignment).filter_by(dataset_id=dataset_id).first()
        if assignment:
            session.delete(assignment)
            session.commit()
        
        return {'success': True, 'dataset_id': dataset_id}
    except Exception as e:
        session.rollback()
        raise ValidationError(f'Error removing theme: {str(e)}')
        
def update_user_theme_role(context, data_dict):
    """Kullanıcının tema rolünü güncelle"""
    from ckan.model.theme import UserThemeAssignment
    from ckan.model import Session
    
    _check_access('sysadmin', context, data_dict)
    
    user_id = data_dict.get('user_id')
    theme_slug = data_dict.get('theme_slug')
    new_role = data_dict.get('role')
    
    if not user_id or not theme_slug or not new_role:
        raise ValidationError('user_id, theme_slug and role are required')
    
    if new_role not in ['member', 'editor', 'admin']:
        raise ValidationError('role must be one of: member, editor, admin')
    
    try:
        session = Session()
        
        assignment = session.query(UserThemeAssignment).filter_by(
            user_id=user_id,
            theme_slug=theme_slug
        ).first()
        
        if not assignment:
            raise NotFound('User theme assignment not found')
        
        assignment.role = new_role
        assignment.assigned_by = context.get('user')
        session.commit()
        
        return {'success': True, 'role': new_role}
    except Exception as e:
        session.rollback()
        if isinstance(e, (ValidationError, NotFound)):
            raise
        raise ValidationError(f'Error updating user theme role: {str(e)}')