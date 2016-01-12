#########
# Copyright (c) 2015 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#  * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  * See the License for the specific language governing permissions and
#  * limitations under the License.

import os
from urlparse import urlparse
from time import sleep

from fabric.api import cd, run, sudo, prefix

from cloudify import ctx
from fabric_plugin.tasks import FabricTaskError
from cloudify.exceptions import NonRecoverableError


def get_preferred_downloader():

    if 'preferred_downloader' not in \
            ctx.instance.runtime_properties.keys():

        preferred_downloader = ''

        try:
            preferred_downloader = run('which wget')
        except FabricTaskError:
            pass

        if not preferred_downloader:
            try:
                preferred_downloader = run('which curl')
            except FabricTaskError:
                pass

        ctx.instance.runtime_properties['preferred_downloader'] = \
            preferred_downloader

    return ctx.instance.runtime_properties['preferred_downloader']


def get_preferred_package_manager():

    if 'preferred_package_manager' not in \
            ctx.instance.runtime_properties.keys():

        preferred_package_manager = ''

        try:
            preferred_package_manager = run('which apt-get')
        except FabricTaskError:
            pass

        if not preferred_package_manager:
            try:
                preferred_package_manager = run('which yum')
            except FabricTaskError:
                pass

        ctx.instance.runtime_properties['preferred_package_manager'] = \
            preferred_package_manager

    return ctx.instance.runtime_properties['preferred_package_manager']


def download_archive_and_save(filename, working_directory):

    command = get_download_command(filename, ctx.node.properties['source_url'])

    with cd(working_directory):
        run(command)

    ctx.instance.runtime_properties['archive_path'] = \
        os.path.join(working_directory,
                     filename)


def get_download_command(filename, source):

    preferred_downloader = get_preferred_downloader()

    if 'wget' in preferred_downloader:
        command = 'wget -O {0} {1}'.format(
            filename, source)
    elif 'curl' in preferred_downloader:
        command = 'curl -L -o {0} {1}'.format(
            filename, source)
    else:
        raise NonRecoverableError(
            'Neither curl nor wget available on target system.')

    return command


def install_package(package):

    preferred_package_manager = get_preferred_package_manager()

    if check_if_package_installed(preferred_package_manager, package):
        return

    command = '{0} -y install {1}'.format(preferred_package_manager, package)

    with cd(cloudify_temp_directory):
        try:
            sudo(command)
        except FabricTaskError as e:
            if 'apt-get update' in str(e):
                sudo('apt-get update')

    return check_if_package_installed(preferred_package_manager, package)


def check_if_package_installed(package_manager, package):

    if 'apt-get' in package_manager:
        command = 'dpkg -s {0}'.format(package)
    elif 'yum' in package_manager:
        command = 'yum list installed {0}'.format(package)
    else:
        raise NonRecoverableError('Only Yum and Apt-get are supported right now.')

    installed = False

    try:
        installed = run(command)
    except FabricTaskError:
        pass

    if not installed:
        return False

    return True


def run_bg(cmd):
    return run('screen -d -m {0}; sleep 1'.format(cmd))

##TODO: Add get preferred extractor function

def extract_to_path(archive_file, working_directory, save_directory=None):

    install_package('unzip')

    if archive_file.endswith(ZIP):
        extract_command = 'unzip {0}'.format(archive_file)
        extract_into = '-d {0}'.format(save_directory)
    elif archive_file.endswith(TGZ) or archive_file.endswith(TARGZ):
        extract_command = 'tar xzvf {0}'.format(archive_file)
        extract_into = '-C {0}'.format(save_directory)
    else:
        raise NonRecoverableError(
            'Only .zip, .tar.gz, and .tgz are accepted.')

    with cd(working_directory):

        if save_directory:
            run('[[ -d {0} ]] || mkdir {0}'.format(save_directory))
            extract_command = '{0} {1}'.format(extract_command, extract_into)

        list_of_extracted_files = run(extract_command)
        ctx.logger.info('extracted {0}'.format(archive_file))
        run('rm {0}'.format(archive_file))
        ctx.logger.info('deleted the archive file {0} after extraction'.format(archive_file))

    extracted_files_root = min(list_of_extracted_files.split('\n'), key=len)[:-1]
    ctx.logger.info('The root of extracted files: {0}'.format(extracted_files_root))
    return extracted_files_root


def get_response_code(host, port):

    preferred_downloader = get_preferred_downloader()

    if 'wget' in preferred_downloader:
        command = 'wget --spider -S "http://{0}:{1}" 2>&1 ' \
                  '| grep "HTTP/" | awk \'{{print $2}}\' | tail' \
                  ' -1'.format(host, port)
    elif 'curl' in preferred_downloader:
        command = 'curl -s -o /dev/null -w "%{{http_code}}" ' \
                  'http://{0}:{1}'.format(host, port)
    else:
        raise NonRecoverableError(
            'Neither curl nor wget available on target system.')

    with cd(cloudify_temp_directory):
        response_code = run(command)

    return response_code


def wait_for_server(host, port, checks=120, interval=1):

    for x in range(0, checks):
        x += 1
        response_code = get_response_code(host, port)
        ctx.logger.info(
            '[GET] http://localhost:{0} {1}'
            .format(port, response_code)
        )
        if str(200) in response_code:
            return True
        else:
            sleep(interval)
    return False


def kill_process():
    command = 'kill -9 {0}'.format(
        ctx.instance.runtime_properties['pid']
    )
    sudo(command)


def install_mongo():
    """ This is the create operation.
    :return:
    """

    ctx.logger.info('Installing Mongo')

    root_path_directory_name = 'mongo_db'
    mongo_data_directory_name = 'data'

    run('[[ -d {0} ]] || mkdir {0}'.format(cloudify_temp_directory))

    parsed_url = urlparse(ctx.node.properties['source_url'])
    filename = os.path.basename(parsed_url.path)
    binaries_directory_name, _ = os.path.splitext(filename)
    if binaries_directory_name.endswith('.tar'):
        binaries_directory_name, _ = os.path.splitext(binaries_directory_name)

    download_archive_and_save(filename, cloudify_temp_directory)
    archive_file = \
        os.path.basename(
            ctx.instance.runtime_properties['archive_path'])
    extract_to_path(archive_file, cloudify_temp_directory)

    with cd(cloudify_temp_directory):
        run('[[ -d {0} ]] || mkdir {0}'.format(root_path_directory_name))
        run('[[ -d {0} ]] || mkdir {0}'.format(mongo_data_directory_name))

    ctx.instance.runtime_properties['mongo_root_path'] = os.path.join(
        cloudify_temp_directory, root_path_directory_name)
    ctx.instance.runtime_properties['mongo_data_path'] = os.path.join(
        cloudify_temp_directory, mongo_data_directory_name)
    ctx.instance.runtime_properties['mongo_binaries_path'] = os.path.join(
        cloudify_temp_directory, binaries_directory_name)

    ctx.logger.info('Installed Mongo.')


def start_mongo():

    ctx.logger.info('Starting Mongo.')

    install_package('screen')

    actual_command = '{0}/bin/mongod --port {1} --dbpath {2} --rest ' \
                     '--journal --shardsvr --smallfiles' \
                     .format(ctx.instance.runtime_properties['mongo_binaries_path'],
                             ctx.node.properties['port'],
                             ctx.instance.runtime_properties['mongo_data_path'])

    ctx.logger.info('running {0}'.format(actual_command))

    with cd(cloudify_temp_directory):
        run_bg(actual_command)
        pid = run('pgrep mongod')

    ctx.logger.info('{0}'.format(pid))

    rest_port = ctx.node.properties['port'] + 1000

    if wait_for_server('localhost', rest_port):
        ctx.instance.runtime_properties['pid'] = pid
    else:
        raise NonRecoverableError('Unable to verify that mongo db started')

    ctx.logger.info('Mongo started.')


def stop_mongo():
    ctx.logger.info('Stopping Mongo.')
    kill_process()
    ctx.logger.info('Stopped Mongo.')


def set_mongo_url(ip=None):

    ctx.source.instance.runtime_properties['mongo_ip_address'] = \
        ip if ip else ctx.target.instance.host_ip
    ctx.source.instance.runtime_properties['mongo_port'] = \
        ctx.target.node.properties['port']


def install_nodejs():
    """ This is the create operation.
    :return:
    """

    ctx.logger.info('Installing NodeJS')

    run('[[ -d {0} ]] || mkdir {0}'.format(cloudify_temp_directory))

    parsed_url = urlparse(ctx.node.properties['source_url'])
    filename = os.path.basename(parsed_url.path)
    binaries_directory_name, _ = os.path.splitext(filename)
    if binaries_directory_name.endswith('.tar'):
        binaries_directory_name, _ = os.path.splitext(binaries_directory_name)

    download_archive_and_save(filename, cloudify_temp_directory)
    archive_file = \
        os.path.basename(
            ctx.instance.runtime_properties['archive_path'])
    extract_to_path(archive_file, cloudify_temp_directory)

    ctx.instance.runtime_properties['nodejs_binaries_path'] = os.path.join(
        cloudify_temp_directory, binaries_directory_name)

    ctx.logger.info('Installed NodeJS')


def set_nodejs_root():
    """ This is the create operation.
    :return:
    """
    ctx.source.instance.runtime_properties['nodejs_binaries_path'] = \
        ctx.target.instance.runtime_properties['nodejs_binaries_path']


def install_application():
    """ This is the create operation.
    :return:
    """

    ctx.logger.info('Installing Application')

    run('[[ -d {0} ]] || mkdir {0}'.format(cloudify_temp_directory))

    parsed_url = urlparse(ctx.node.properties['source_url'])
    filename = os.path.basename(parsed_url.path)
    binaries_directory_name, _ = os.path.splitext(filename)
    if binaries_directory_name.endswith('.tar'):
        binaries_directory_name, _ = os.path.splitext(binaries_directory_name)

    download_archive_and_save(filename, cloudify_temp_directory)
    archive_file = \
        os.path.basename(
            ctx.instance.runtime_properties['archive_path'])
    application_source = extract_to_path(archive_file, cloudify_temp_directory)

    ctx.logger.info('application source = {0}'.format(application_source))

    ctx.instance.runtime_properties['application_source'] = os.path.join(
        cloudify_temp_directory, application_source)

    nodejs_binaries_dir = ctx.instance.runtime_properties['nodejs_binaries_path']
    command = '{0}/bin/npm install'.format(nodejs_binaries_dir)

    with cd(ctx.instance.runtime_properties['application_source']):
        run(command)

    ctx.logger.info('Installed Application')


def start_application():
    command = '{0}/bin/node {1}/{2}'.format(
        ctx.instance.runtime_properties['nodejs_binaries_path'],
        ctx.instance.runtime_properties['application_source'],
        ctx.node.properties['startup_script']
    )
    ctx.logger.info('running {0}'.format(command))

    install_package('screen')

    exports = 'MONGO_HOST={0} MONGO_PORT={1} NODECELLAR_PORT={2}'\
              .format(ctx.instance.runtime_properties['mongo_ip_address'],
                      ctx.instance.runtime_properties['mongo_port'],
                      ctx.node.properties['port'])
    with prefix('export {0}'.format(exports)):
        run_bg(command)
        pid = run('pgrep node')
    if wait_for_server('localhost',
                       ctx.node.properties['port']):
        ctx.instance.runtime_properties['pid'] = pid
    else:
        raise NonRecoverableError('Unable to verify that node application started')


def stop_application():
    ctx.logger.info('stopping node application')
    kill_process()
    ctx.logger.info('stopped node application')


cloudify_temp_directory = os.path.join('/tmp', ctx.execution_id)
ZIP = '.zip'
TGZ = '.tgz'
TARGZ = '.tar.gz'
