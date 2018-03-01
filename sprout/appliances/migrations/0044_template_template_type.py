# -*- coding: utf-8 -*-
# Generated by Django 1.9.13 on 2018-02-28 10:09
from __future__ import unicode_literals

from django.db import migrations, models


def add_type_to_template(apps, schema_editor):
    Template = apps.get_model("appliances", "Template")  # noqa
    # Normalize the container entries if they ever been touched
    Template.objects.using(schema_editor.connection.alias)\
        .filter(container='')\
        .update(container=None)
    # So, no container, VM as usual
    Template.objects.using(schema_editor.connection.alias)\
        .filter(container=None)\
        .update(template_type='virtual_machine')
    # Container but not an openshift - docker
    Template.objects.using(schema_editor.connection.alias)\
        .exclude(container=None, provider__provider_type='openshift')\
        .update(template_type='docker_vm')

    # Container and openshift - openshift pods
    Template.objects.using(schema_editor.connection.alias)\
        .exclude(container=None).filter(provider__provider_type='openshift')\
        .update(template_type='openshift_pod')


class Migration(migrations.Migration):

    dependencies = [
        ('appliances', '0043_provider_provider_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='template',
            name='template_type',
            field=models.CharField(
                choices=[
                    (b'virtual_machine', b'Virtual Machine'),
                    (b'docker_vm', b'VM-based Docker container'),
                    (b'openshift_pod', b'Openshift pod')],
                default=b'virtual_machine', max_length=16),
        ),
        migrations.RunPython(add_type_to_template),
    ]