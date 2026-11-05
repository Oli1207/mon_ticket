from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

ALLOWED_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']

image_extension_validator = FileExtensionValidator(ALLOWED_IMAGE_EXTENSIONS)


def validate_image_size(file):
    if file.size > settings.MAX_IMAGE_UPLOAD_BYTES:
        limit_mb = settings.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(f"L'image ne doit pas depasser {limit_mb} Mo.")


IMAGE_VALIDATORS = [image_extension_validator, validate_image_size]
