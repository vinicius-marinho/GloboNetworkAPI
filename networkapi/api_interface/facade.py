# -*- coding:utf-8 -*-

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

from django.template import Context, Template
import thread

from networkapi.extra_logging import local
from networkapi.settings import INTERFACE_TOAPPLY_PATH, INTERFACE_CONFIG_TEMPLATE_PATH
from networkapi.distributedlock import LOCK_INTERFACE_EQUIP_CONFIG
from networkapi.log import Log

from networkapi.interface.models import Interface, PortChannel
from networkapi.util import is_valid_int_greater_zero_param
from networkapi.api_interface import exceptions
from networkapi.equipamento.models import EquipamentoRoteiro
from networkapi.roteiro.models import TipoRoteiro
from networkapi.api_deploy.facade import deploy_config_in_equipment_synchronous

SUPPORTED_EQUIPMENT_BRANDS = ["Cisco"]
TEMPLATE_TYPE = "interface_configuration"

log = Log(__name__)

def generate_and_deploy_interface_config(user, id_interface):

    if not is_valid_int_greater_zero_param(id_interface):
        raise exceptions.InvalidIdInterfaceException()

    interface = Interface.get_by_pk(id_interface)
    interfaces = [interface]

    file_to_deploy = _generate_config_file(interfaces)

    #TODO Deploy config file
    lockvar = LOCK_INTERFACE_EQUIP_CONFIG % (interface.equipamento.id)
    status_deploy = deploy_config_in_equipment_synchronous(file_to_deploy, interface.equipamento, lockvar)

    return status_deploy

def generate_and_deploy_channel_config(user, id_channel):

    if not is_valid_int_greater_zero_param(id_channel):
        raise exceptions.InvalidIdInterfaceException()

    channel = PortChannel.get_by_pk(id_channel)

    interfaces = channel.list_interfaces()

    #group interfaces by equipment
    equipment_interfaces = dict()
    for interface in interfaces:
        if interface.equipamento.id not in equipment_interfaces:
            equipment_interfaces[interface.equipamento.id] = []
        equipment_interfaces[interface.equipamento.id].append(interface)

    files_to_deploy = {}
    for equipment_id in equipment_interfaces.keys():
        grouped_interfaces = equipment_interfaces[equipment_id]
        file_to_deploy = _generate_config_file(grouped_interfaces)
        files_to_deploy[equipment_id] = file_to_deploy

    #TODO Deploy config file
    #make separate threads
    for equipment_id in files_to_deploy.keys():
        lockvar = LOCK_INTERFACE_EQUIP_CONFIG % (interface.equipamento.id)
        equipamento = Equipamento.get_by_pk(equipment_id)
        status_deploy = deploy_config_in_equipment_synchronous(files_to_deploy[equipment_id], equipamento, lockvar)

    return status_deploy

def _generate_config_file(interfaces):

    #check if all interfaces are configuring same equipment
    #raises error if not
    equipment_interfaces = dict()
    equipment_interfaces[interface[0].equipamento.nome] = 1
    for interface in interfaces:
        if interface.equipamento.nome not in equipment_interfaces:
            log.error("Error trying to configure multiple interfaces in different equipments in same call.")
            raise exceptions.InterfaceTemplateException

    equipment_id = interfaces[0].equipamento.id
    equipment_template = EquipamentoRoteiro.search(None, equipment_id, TEMPLATE_TYPE)
    if len(equipment_template) != 1:
        raise exceptions.InterfaceTemplateException()

    filename_in = INTERFACE_CONFIG_TEMPLATE_PATH+"/"+equipment_template.roteiro.roteiro

    request_id = getattr(local, 'request_id', NO_REQUEST_ID)
    filename_out = "int_id_"+interface[0].id+"_config_"+request_id

    # Read contents from file
    try:
        file_handle = open(filein, 'r')
        template_file = Template ( file_handle.read() )
        file_handle.close()
    except IOError, e:
        log.error("Error opening template file for read: %s" % filein)
        raise e

    key_dict = _generate_dict(interfaces)

    #Render the template
    try:
        config_to_be_saved = template_file.render( Context(key_dict) )
    except KeyError, exception:
        raise InvalidKeyException(exception)

    #Save new file
    try:
        file_handle = open(filename_out, 'w')
        file_handle.write(config_to_be_saved)
        file_handle.close()
    except IOError, e:
        log.error("Error writing to config file: %s" % fileout)
        raise e

    rel_file_to_deploy = INTERFACE_TOAPPLY_PATH+filename_out

    return rel_file_to_deploy

def _generate_dict(interfaces):

    #Check if it is a supported equipment interface
    if interface.equipamento.modelo.marca not in SUPPORTED_EQUIPMENT_BRANDS:
        raise exceptions.UnsupportedEquipmentException()

    key_dict = {}
    key_dict['interfaces'] = {}
    #TODO Separate differet vendor support
    #Cisco Nexus 6001 dict
    for interface in interfaces:
        key_dict['interfaces'][interface.id] = {}
    key_dict['interfaces'][interface.id]["NATIVE_VLAN"] = interface.vlan_nativa
    key_dict['interfaces'][interface.id]["VLAN_RANGE"] = get_vlan_range(interface)
    key_dict['interfaces'][interface.id]["USE_MCLAG"] = 1
    key_dict['interfaces'][interface.id]["MCLAG_IDENTIFIER"] = int ( re.sub(r"[a-zA\-]", "", interface.channel.name) )
    key_dict['interfaces'][interface.id]["INTERFACE_NAME"] = interface.interface
    key_dict['interfaces'][interface.id]["INTERFACE_DESCRIPTION"] = "description to be defined"
    key_dict['interfaces'][interface.id]["INTERFACE_TYPE"] = interface.tipo.tipo
    if interface.channel is not None:
        key_dict["BOOL_INTERFACE_IN_CHANNEL"] = 1
        key_dict["PORTCHANNEL_NAME"] = interface.channel.name
        if interface.channel.lacp:
            key_dict['interfaces'][interface.id]["CHANNEL_LACP_MODE"] = "active"
        else:
            key_dict['interfaces'][interface.id]["CHANNEL_LACP_MODE"] = "on"

    else:
        key_dict["BOOL_INTERFACE_IN_CHANNEL"] = 0


    return key_dict

