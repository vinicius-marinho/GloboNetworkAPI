# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import httplib2
from json import dumps
from time import sleep
import re
import socket
from CumulusExceptions import *
from networkapi.equipamento.models import EquipamentoAcesso
from ..base import BasePlugin
from .. import exceptions
from networkapi.api_rest import exceptions as api_exceptions


log = logging.getLogger(__name__)


class Cumulus(BasePlugin):
    """Cumulus Plugin"""
    # httplib2 configurations
    HTTP = httplib2.Http('.cache', disable_ssl_certificate_validation=True)
    HEADERS = {'Content-Type': 'application/json; charset=UTF-8'}
    httplib2.RETRIES = 3
    # Cumulus commands to control the staging area
    COMMIT = {'cmd': 'commit'}
    ABORT_CHANGES = {'cmd': 'abort'}
    PENDING = {'cmd': 'pending'}
    # Expected strings when something occurs not expected occurs
    WARNINGS = 'WARNING: Committing these changes will cause problems'
    COMMIT_CONCURRENCY = 'Multiple users are currently'
    COMMON_USERS = 'cumulus|root'
    ALREADY_EXISTS = 'configuration already has'
    # Variables needed in the configuration below
    MAX_WAIT = 5
    MAX_RETRIES = 3
    SLEEP_WAIT_TIME = 5
    _command_list = []
    device = None
    username = None
    password = None

    def _get_info(self):
        """Get info from database to access the device"""
        if self.equipment_access is None:
            try:
                self.equipment_access = EquipamentoAcesso.search(
                    None, self.equipment, 'https').uniqueResult()
            except Exception:
                log.error('Access type %s not found for equipment %s.' %
                          ('https', self.equipment.nome))
                raise exceptions.InvalidEquipmentAccessException()

        self.device = self.equipment_access.fqdn
        self.username = self.equipment_access.user
        self.password = self.equipment_access.password

        self.HTTP.add_credentials(self.username, self.password)

    def connect(self):
        """Use the connect function of the superclass to get
        the informations for access the device"""
        self._get_info()

    def _getConfFromFile(self, filename):
        """Get the configurations needed to be applied
            and insert into a list"""
        try:
            with open(filename, 'r+') as lines:
                for line in lines:
                    self._command_list.append(line)
        except IOError as e:
            log.error('Error opening the file: %s' % filename)
            raise e
        except Exception as e:
            log.error('Error %s when trying to\
                      read the file %s' % (e, filename))
            raise e
        return True

    def _send_request(self, data):
        """Send requests for the equipment"""
        try:
            count = 0
            validResponse = False
            while (count < self.MAX_RETRIES and not validResponse):
                resp, content = self.HTTP.request(
                    self.device, method="POST",
                    headers=self.HEADERS, body=dumps(data))
                if resp.status != 200:
                    count += 1
                    if count >= self.MAX_RETRIES:
                        log.error('Wasn\'t possible to reach the equipment %s\
                                  Validate if it has the\
                                  correct access\
                                  and if nginx and restserver\
                                  are running' % self.equipment.nome)
                        raise MaxRetryAchieved(
                            'Wasn\'t possible to reach the equipment %s\
                                  Validate if it has the\
                                  correct access\
                                  and if nginx and restserver\
                                  are running' % self.equipment.nome)
                else:
                    validResponse = True
        except socket.error as error:
            log.error('Error in socket connection: %s' % error)
            raise error
        except httplib2.ServerNotFoundError as error:
            log.error(
                'Error: %s. Check if the restserver is enabled in %s' %
                (error, self.equipment.nome))
            raise error
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return content

    def _search_pending_warnings(self):
        """Validate if exists any warnings in the staging configuration"""
        try:
            content = self._send_request(self.PENDING)
            check_warning = re.search(
                self.WARNINGS, content, flags=re.IGNORECASE)
            if check_warning:
                log.error('There are warnings in the configuration.\
                          Aborting changes.')
                self._send_request(self.ABORT_CHANGES)
                raise ConfigurationWarning(
                    'The equipment is raising warnings\
                    because of problems in the configuration.\
                    Aborting changes.')
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return True

    def _check_pending(self):
        """Verify if exists any configuration in the staging area
           made by another user"""
        try:
            count = 0
            validResponse = False
            while (count < self.MAX_WAIT and not validResponse):
                content = self._send_request(self.PENDING)
                check_concurrency = re.search(
                    self.COMMIT_CONCURRENCY, content, flags=re.IGNORECASE)
                check_users = re.search(
                    self.COMMON_USERS, content)
                if check_users or check_concurrency:
                    log.warning(
                        'The configuration staging for %s is been used' %
                        self.equipment.nome)
                    count += 1
                    if count >= self.MAX_WAIT:
                        log.error(
                            'The equipment %s has configuration\
                             pendings for too long.\
                             The process was aborted.' %
                            self.equipment.nome)
                        raise MaxTimeWaitExceeded(
                            'Time waiting the configuration\
                             staging be available exceeded')
                    sleep(self.SLEEP_WAIT_TIME)
                else:
                    validResponse = True
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return True

    def configurations(self):
        """Apply the configurations in equipment
        and search for errors syntax, and if the configurations
        will cause problems in the equipment"""
        try:
            self._check_pending()
            for cmd in self._command_list:
                content = self._send_request({'cmd': cmd})
                check_error = re.search('ERROR:', content, flags=re.IGNORECASE)
                check_existence = re.search(
                    self.ALREADY_EXISTS, content, flags=re.IGNORECASE)
                if check_error:
                    log.error(
                        'Command "%s" not found!\
                        Verify the Syntax. Aborting configurations.' %
                        cmd)
                    self._send_request(self.ABORT_CHANGES)
                    raise ConfigurationError(
                        'Applying Rollback of the configuration\
                        because of errors when applying the commands.')
                elif check_existence:
                    log.info(
                        'The command "%s" already exists in %s' %
                        (cmd, self.equipment.nome))
            check_warnings = self._search_pending_warnings()
            if check_warnings:
                content = self._send_request(self.COMMIT)
        except Exception as error:
            log.error('Error: ' % error)
            raise error
        return content

    def copyScriptFileToConfig(self, filename, use_vrf=None, destination=None):
        """Get the configurations needed for configure the equipment
              from the file generated

              The use_vrf and destination variables won't be used
              """
        try:
            success = self._getConfFromFile(filename)
            if success:
                output = self.configurations()
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return output

    def create_svi(self, svi_number, svi_description):
        """Create SVI in switch."""
        try:
            proceed = self._check_pending()
            if proceed:
                command = 'net add vlan %s alias\
                 %s' % (svi_number, svi_description)
                self._send_request({'cmd': command})
                check_warnings = self._search_pending_warnings()
                if check_warnings:
                    content = self._send_request(self.COMMIT)
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return content

    def remove_svi(self, svi_number):
        """Delete SVI from switch."""
        try:
            proceed = self._check_pending()
            if proceed:
                command = 'net del vlan %s' % svi_number
                content = self._send_request({'cmd': command})
                check_warnings = self._search_pending_warnings()
                if check_warnings:
                    content = self._send_request(self.COMMIT)
        except Exception as error:
            log.error('Error: %s' % error)
            raise error
        return content

    def ensure_privilege_level(self, privilege_level=None):
        """Cumulus don't use the concept of privilege level"""
        pass

    def close(self):
        """This configuration file won't use ssh connections"""
        pass

    def exec_command(
            self,
            command,
            success_regex=None,
            invalid_regex=None,
            error_regex=None):
        """The exec command will not be needed here"""
        pass