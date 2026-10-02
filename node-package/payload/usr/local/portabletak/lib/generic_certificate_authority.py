# Generated from reviewed OTS source; generic runtime only.
import shlex as _pt_shlex
import secrets as _pt_secrets
from xml.sax.saxutils import escape as _pt_xml_escape

def _pt_arg(value):
    return _pt_shlex.quote(str(value))

def _pt_openssl(command):
    args = _pt_shlex.split(command)
    cwd = None
    if args[:1] == ['cd']:
        if len(args) < 4 or args[2] != '&&':
            raise RuntimeError('invalid_crypto_command')
        cwd, args = (args[1], args[3:])
    if not args or args[0] != 'openssl':
        raise RuntimeError('invalid_crypto_command')
    environment = os.environ.copy()
    for index, arg in enumerate(args):
        if index and args[index - 1] in ('-passin', '-passout'):
            if not arg.startswith('pass:'):
                raise RuntimeError('invalid_crypto_password_source')
            variable = 'PORTABLETAK_CRYPTO_' + str(index)
            environment[variable] = arg[5:]
            args[index] = 'env:' + variable
    result = subprocess.run(args, cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
    return result.returncode
import io
import os
import random
import re
import shutil
import subprocess
import traceback
import uuid
import zipfile
from pathlib import Path
from shutil import copyfile, rmtree
from urllib.parse import urlparse
from flask import request
from jinja2 import Template
from .ca_config import ca_config, server_config

class CertificateAuthority:

    def __init__(self, logger, app):
        self.logger = logger
        self.app = app

    def create_ca(self):
        if not self.check_if_ca_exists():
            self.logger.info('Creating CA...')
            os.makedirs(self.app.config.get('OTS_CA_FOLDER'), exist_ok=True)
            f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca_config.cfg'), 'w')
            f.write(ca_config)
            f.close()
            subject = self.app.config.get('OTS_CA_SUBJECT') + '/CN={}'.format(self.app.config.get('OTS_CA_NAME'))
            command = 'openssl req -new -sha256 -x509 -days {} -extensions v3_ca -keyout {} -out {} -passout pass:{} -config {} -subj {}'.format(_pt_arg(self.app.config.get('OTS_CA_EXPIRATION_TIME')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-do-not-share.key')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca_config.cfg')), _pt_arg(subject))
            pass
            exit_code = _pt_openssl(command)
            if exit_code:
                raise Exception('Failed to create ca.pem. Exit code {}'.format(exit_code))
            command = 'openssl x509 -in {} -addtrust clientAuth -addtrust serverAuth -setalias {} -out {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(self.app.config.get('OTS_CA_NAME')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-trusted.pem')))
            pass
            exit_code = _pt_openssl(command)
            if exit_code:
                raise Exception('Failed to add trust to CA. Exit code {}'.format(exit_code))
            use_legacy = not _pt_openssl('openssl list -providers')
            if use_legacy:
                command = 'openssl pkcs12 -legacy -export -in {} -out {} -passout pass:{} -nokeys -caname {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-trusted.pem')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'truststore-root.p12')), _pt_arg(self.app.config['PORTABLETAK_TRUSTSTORE_PASSWORD']), _pt_arg(self.app.config.get('OTS_CA_NAME')))
            else:
                command = 'openssl pkcs12 -export -in {} -out {} -passout pass:{} -nokeys -caname {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-trusted.pem')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'truststore-root.p12')), _pt_arg(self.app.config['PORTABLETAK_TRUSTSTORE_PASSWORD']), _pt_arg(self.app.config.get('OTS_CA_NAME')))
            pass
            exit_code = _pt_openssl(command)
            if exit_code:
                raise Exception('Failed to export truststore. Exit code {}'.format(exit_code))
            Path(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'crl_index.txt')).touch()
            f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'crl_index.txt.attr'), 'w')
            f.write('unique_subject = no')
            f.close()
            command = 'cd {} && openssl ca -config {} -gencrl -keyfile {} -passin pass:{} -cert {} -out {}'.format(_pt_arg(self.app.config.get('OTS_CA_FOLDER')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca_config.cfg')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-do-not-share.key')), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.crl')))
            pass
            exit_code = _pt_openssl(command)
            if exit_code:
                raise Exception('Failed to create crl. Exit code {}'.format(exit_code))
            self.logger.debug('Creating server cert...')
            self.issue_certificate('opentakserver', True)
            self.logger.info("Certificate authority created successfully. You may need to restart nginx if it's proxying SSL requests.")
        else:
            self.logger.debug('CA already exists')

    def issue_certificate(self, common_name, server=False):
        if not isinstance(common_name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}', common_name):
            raise ValueError('invalid_certificate_name')
        if not os.path.exists(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')):
            raise FileNotFoundError('ca.pem not found')
        if os.path.exists(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name)):
            shutil.rmtree(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name))
        os.makedirs(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name))
        subject = self.app.config.get('OTS_CA_SUBJECT') + '/CN={}'.format(common_name)
        command = 'openssl req -new -newkey rsa:2048 -sha256 -keyout {} -passout pass:{} -out {} -subj {} -config {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.key')), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.csr')), _pt_arg(subject), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca_config.cfg')))
        pass
        exit_code = _pt_openssl(command)
        if exit_code:
            raise Exception('Failed to create csr. Exit code {}'.format(exit_code))
        csr = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.csr'), 'r')
        csr_bytes = csr.read().encode()
        csr.close()
        self.sign_csr(csr_bytes, common_name, server)
        use_legacy = not _pt_openssl('openssl list -providers')
        if use_legacy:
            command = 'openssl pkcs12 -legacy -export -in {}.pem -inkey {}.key -out {}.p12 -name {} -CAfile {} -passin pass:{} -passout pass:{}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(common_name), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(self.app.config['PORTABLETAK_CLIENT_PASSWORD']))
        else:
            command = 'openssl pkcs12 -export -in {}.pem -inkey {}.key -out {}.p12 -name {} -CAfile {} -passin pass:{} -passout pass:{}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name)), _pt_arg(common_name), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(self.app.config['PORTABLETAK_CLIENT_PASSWORD']))
        pass
        exit_code = _pt_openssl(command)
        if exit_code:
            raise Exception('Failed to export p12 key. Exit code {}'.format(exit_code))
        os.chmod(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.key'), 400)
        command = 'openssl rsa -in {} -passin pass:{} -out {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.key')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_PASSWORD'))), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.nopass.key')))
        pass
        exit_code = _pt_openssl(command)
        if exit_code:
            raise Exception('Failed to remove server key password. Exit code {}'.format(exit_code))
        if not server:
            return self.generate_zip(common_name)
        else:
            command = 'openssl x509 -pubkey -in {} -out {}'.format(_pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.pem')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.pub')))
            pass
            exit_code = _pt_openssl(command)
            if exit_code:
                raise Exception("Failed to generate server's public key. Exit code {}".format(exit_code))

    def sign_csr(self, csr_bytes, common_name, server=False):
        if not isinstance(common_name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}', common_name):
            raise ValueError('invalid_certificate_name')
        os.makedirs(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name), exist_ok=True)
        f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.csr'), 'wb')
        f.write(csr_bytes)
        f.close()
        if server:
            if re.match('^[0-9]{1,3}.[0-9]{1,3}.[0-9]{1,3}.[0-9]{1,3}$', common_name):
                alt_name_field = 'IP.1'
            else:
                alt_name_field = 'DNS.1'
            f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, '{}_config.cfg'.format(common_name)), 'w')
            f.write(server_config.render(alt_name_field='IP.1', common_name=self.app.config['OTS_SERVER_ADDRESS']))
            f.close()
            config_file = os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, '{}_config.cfg'.format(common_name))
            extensions = 'server'
        else:
            config_file = os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca_config.cfg')
            extensions = 'client'
        command = 'openssl x509 -sha256 -req -days {} -in {} -CA {} -CAkey {} -out {} -set_serial {} -passin pass:{} -extensions {} -extfile {}'.format(_pt_arg(self.app.config.get('OTS_CA_EXPIRATION_TIME')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.csr')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca-do-not-share.key')), _pt_arg(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.pem')), _pt_arg(_pt_secrets.randbits(159) or 1), _pt_arg(self.app.config.get('OTS_CA_PASSWORD')), _pt_arg(extensions), _pt_arg(config_file))
        pass
        exit_code = _pt_openssl(command)
        if exit_code:
            raise Exception('Failed to sign csr. Exit code {}'.format(exit_code))
        if server:
            with open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem'), 'r') as ca_file:
                f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.pem'), 'a')
                f.write(ca_file.read())
                f.close()
        f = open(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, common_name + '.pem'), 'r')
        cert_bytes = f.read().encode()
        f.close()
        return cert_bytes

    def check_if_ca_exists(self):
        return os.path.exists(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'ca.pem'))

    def generate_zip(self, common_name):
        if not isinstance(common_name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}', common_name):
            raise ValueError('invalid_certificate_name')
        truststore = os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'truststore-root.p12')
        user_p12 = os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name, '{}.p12'.format(common_name))
        user_file_path = os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'certs', common_name)
        random_id = uuid.uuid4()
        new_uid = uuid.uuid4()
        parent_folder = '80b828699e074a239066d454a76284eb'
        folder = '5c2bfcae3d98c9f4d262172df99ebac5'
        pref_file_template = Template('<?xml version=\'1.0\' standalone=\'yes\'?>\n                <preferences>\n                    <preference version="1" name="cot_streams">\n                        <entry key="count" class="class java.lang.Integer">1</entry>\n                        <entry key="description0" class="class java.lang.String">OpenTAKServer_{{ server }}</entry>\n                        <entry key="enabled0" class="class java.lang.Boolean">true</entry>\n                        <entry key="connectString0" class="class java.lang.String">{{ server }}:{{ ssl_port }}:ssl</entry>\n                    </preference>\n                    <preference version="1" name="com.atakmap.app_preferences">\n                        <entry key="deviceProfileEnableOnConnect" class="class java.lang.Boolean">true</entry>\n                        <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>\n                        <entry key="caLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/{{ server_filename }}</entry>\n                        <entry key="caPassword" class="class java.lang.String">{{ cert_password }}</entry>\n                        <entry key="clientPassword" class="class java.lang.String">{{ cert_password }}</entry>\n                        <entry key="certificateLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/{{ user_filename }}</entry>\n                        <entry key="appMgmtEnableUpdateServer" class="class java.lang.Boolean">true</entry>\n                        <entry key="atakUpdateServerUrl" class="class java.lang.String">https://{{ server }}:{{ marti_port }}/api/packages</entry>\n                        <entry key="repoStartupSync" class="class java.lang.Boolean">true</entry>\n                        <entry key="updateServerCaLocation" class="class java.lang.String">/storage/emulated/0/atak/cert/{{ server_filename }}</entry>\n                        <entry key="updateServerCaPassword" class="class java.lang.String">{{ cert_password }}</entry>\n                    </preference>\n                </preferences>\n                ')
        manifest_file_template = Template('<MissionPackageManifest version="2">\n                   <Configuration>\n                      <Parameter name="uid" value="{{ uid }}"/>\n                      <Parameter name="name" value="OpenTAKServer_{{ server }}"/>\n                      <Parameter name="onReceiveDelete" value="true"/>\n                   </Configuration>\n                   <Contents>\n                      <Content ignore="false" zipEntry="{{ folder }}/preference.pref"/>\n                      <Content ignore="false" zipEntry="{{ folder }}/{{ server_filename }}"/>\n                      <Content ignore="false" zipEntry="{{ folder }}/{{ user_filename }}"/>\t  \n                   </Contents>\n                </MissionPackageManifest>\n                ')
        manifest_file_parent_template = Template('<MissionPackageManifest version="2">\n                       <Configuration>\n                          <Parameter name="uid" value="{{ uid }}"/>\n                          <Parameter name="name" value="OpenTAKServer_{{ server }}_CONFIG"/>\n                       </Configuration>\n                       <Contents>\n                          <Content ignore="false" zipEntry="{{ folder }}/{{ internal_dp_name }}.zip"/>\n                       </Contents>\n                    </MissionPackageManifest>\n                    ')
        pref = pref_file_template.render(server=self.app.config['OTS_SERVER_ADDRESS'], marti_port=self.app.config.get('OTS_MARTI_HTTPS_PORT'), server_filename='truststore-root.p12', user_filename=f'{common_name}.p12', cert_password=_pt_xml_escape(self.app.config['PORTABLETAK_CLIENT_PASSWORD']), ssl_port=self.app.config.get('OTS_SSL_STREAMING_PORT'))
        man = manifest_file_template.render(uid=random_id, server=self.app.config['OTS_SERVER_ADDRESS'], server_filename='truststore-root.p12', user_filename=f'{common_name}.p12', folder=folder)
        man_parent = manifest_file_parent_template.render(uid=new_uid, server=self.app.config['OTS_SERVER_ADDRESS'], folder=parent_folder, internal_dp_name=common_name)
        if not os.path.exists(os.path.join(user_file_path, folder)):
            os.makedirs(os.path.join(user_file_path, folder))
        if not os.path.exists(os.path.join(user_file_path, 'MANIFEST')):
            os.makedirs(os.path.join(user_file_path, 'MANIFEST'))
        with open(os.path.join(user_file_path, folder, 'preference.pref'), 'w') as pref_file:
            pref_file.write(pref)
        with open(os.path.join(user_file_path, 'MANIFEST', 'manifest.xml'), 'w') as manifest_file:
            manifest_file.write(man)
        self.logger.debug('Generating inner Data Package: {}.zip'.format(common_name))
        copyfile(truststore, os.path.join(user_file_path, folder, 'truststore-root.p12'))
        self.logger.debug('Copying {} to {}'.format(truststore, os.path.join(user_file_path, folder, 'truststore-root.p12')))
        copyfile(user_p12, os.path.join(user_file_path, folder, '{}.p12'.format(common_name)))
        zipf = zipfile.ZipFile(os.path.join(user_file_path, '{}.zip'.format(common_name)), 'w', zipfile.ZIP_DEFLATED)
        os.chdir(os.path.join(user_file_path))
        for root, dirs, files in os.walk(folder):
            for file in files:
                zipf.write(os.path.join(root, file))
        for root, dirs, files in os.walk('MANIFEST'):
            for file in files:
                self.logger.debug('adding {} to zip'.format(os.path.join(root, file)))
                zipf.write(os.path.join(root, file))
        zipf.close()
        rmtree(os.path.join(user_file_path, 'MANIFEST'))
        rmtree(os.path.join(user_file_path, folder))
        if not os.path.exists(os.path.join(user_file_path, parent_folder)):
            os.makedirs(os.path.join(user_file_path, parent_folder))
        if not os.path.exists(os.path.join(user_file_path, 'MANIFEST')):
            os.makedirs(os.path.join(user_file_path, 'MANIFEST'))
        with open(os.path.join(user_file_path, 'MANIFEST', 'manifest.xml'), 'w') as manifest_parent:
            manifest_parent.write(man_parent)
        self.logger.info('Generating Main Data Package: {}_CONFIG.zip'.format(common_name))
        copyfile(os.path.join(user_file_path, '{}.zip'.format(common_name)), os.path.join(user_file_path, parent_folder, '{}.zip'.format(common_name)))
        zipp = zipfile.ZipFile(os.path.join(user_file_path, '{}_CONFIG.zip'.format(common_name)), 'w', zipfile.ZIP_DEFLATED)
        for root, dirs, files in os.walk(parent_folder):
            for file in files:
                zipp.write(os.path.join(root, file))
        for root, dirs, files in os.walk('MANIFEST'):
            for file in files:
                zipp.write(os.path.join(root, file))
        zipp.close()
        itak_preferences = Template('<?xml version=\'1.0\' standalone=\'yes\'?>\n<preferences>\n  <preference version="1" name="cot_streams">\n    <entry key="count" class="class java.lang.Integer">1</entry>\n    <entry key="description0" class="class java.lang.String">OpenTAKServer_{{ server }}</entry>\n    <entry key="enabled0" class="class java.lang.Boolean">true</entry>\n    <entry key="connectString0" class="class java.lang.String">{{ server }}:{{ ssl_port }}:ssl</entry>\n  </preference>\n  <preference version="1" name="com.atakmap.app_preferences">\n    <entry key="displayServerConnectionWidget" class="class java.lang.Boolean">true</entry>\n    <entry key="caLocation" class="class java.lang.String">cert/truststore-root.p12</entry>\n    <entry key="caPassword" class="class java.lang.String">{{ cert_password }}</entry>\n    <entry key="clientPassword" class="class java.lang.String">{{ cert_password }}</entry>\n    <entry key="certificateLocation" class="class java.lang.String">cert/{{ common_name }}.p12</entry>\n  </preference>\n</preferences>\n\n')
        f = open(os.path.join(user_file_path, 'config.pref'), 'w')
        f.write(itak_preferences.render(server=self.app.config['OTS_SERVER_ADDRESS'], ssl_port=self.app.config.get('OTS_SSL_STREAMING_PORT'), cert_password=_pt_xml_escape(self.app.config['PORTABLETAK_CLIENT_PASSWORD']), common_name=common_name))
        f.close()
        self.logger.info('Generating {}_CONFIG_iTAK.zip...'.format(common_name))
        itak_zip = zipfile.ZipFile(os.path.join(user_file_path, '{}_CONFIG_iTAK.zip'.format(common_name)), 'w', zipfile.ZIP_DEFLATED)
        itak_zip.write(os.path.join(user_file_path, 'config.pref'), 'config.pref')
        itak_zip.write(os.path.join(user_file_path, common_name + '.p12'), common_name + '.p12')
        itak_zip.write(os.path.join(self.app.config.get('OTS_CA_FOLDER'), 'truststore-root.p12'), 'truststore-root.p12')
        itak_zip.close()
        rmtree(os.path.join(user_file_path, 'MANIFEST'))
        rmtree(os.path.join(user_file_path, parent_folder))
        os.remove(os.path.join(user_file_path, '{}.zip'.format(common_name)))
        os.remove(os.path.join(user_file_path, 'config.pref'.format(common_name)))
        return ['{}_CONFIG.zip'.format(common_name), '{}_CONFIG_iTAK.zip'.format(common_name)]
