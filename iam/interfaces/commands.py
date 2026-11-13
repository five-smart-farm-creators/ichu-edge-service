"""Command-line interface for the IAM bounded context."""

import os
import uuid

import click
from flask import current_app
from flask.cli import with_appcontext


@click.command("register-device")
@click.argument("device_id", type=click.UUID)
@with_appcontext
def register_device_command(device_id: uuid.UUID) -> None:
    """Register a collar assigned in the cloud, using the key in DEVICE_API_KEY."""
    api_key = os.environ.get("DEVICE_API_KEY", "").strip()
    if not api_key:
        raise click.ClickException("DEVICE_API_KEY must be set in the environment")
    service = current_app.extensions["iam_authentication_service"]
    _, created = service.get_or_create_device(str(device_id), api_key)
    state = "registered" if created else "already registered"
    click.echo(f"Device {device_id} {state}")
