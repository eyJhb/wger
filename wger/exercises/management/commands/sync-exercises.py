# This file is part of wger Workout Manager.
#
# wger Workout Manager is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# wger Workout Manager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License

# Django
from django.core.exceptions import ValidationError
from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.core.validators import URLValidator

# Third Party
import requests
from requests.utils import default_user_agent

# wger
from wger import get_version
from wger.exercises.models import (
    DeletionLog,
    Equipment,
    Exercise,
    ExerciseBase,
    ExerciseCategory,
    ExerciseImage,
    ExerciseVideo,
    Muscle,
)


EXERCISE_API = "{0}/api/v2/exerciseinfo/?limit=100"
DELETION_LOG_API = "{0}/api/v2/deletion-log/?limit=100"
CATEGORY_API = "{0}/api/v2/exercisecategory/"
MUSCLE_API = "{0}/api/v2/muscle/"
EQUIPMENT_API = "{0}/api/v2/equipment/"


class Command(BaseCommand):
    """
    Synchronizes exercise data from a wger instance to the local database
    """
    remote_url = 'https://wger.de'
    headers = {}

    help = """Synchronizes exercise data from a wger instance to the local database.
            This script also deletes entries that were removed on the server such
            as exercises, images or videos.

            Please note that at the moment the following objects can only identified
            by their id. If you added new objects they might have the same IDs as the
            remote ones and will be overwritten:
            - categories
            - muscles
            - equipment
            """

    def add_arguments(self, parser):
        parser.add_argument(
            '--remote-url',
            action='store',
            dest='remote_url',
            default=self.remote_url,
            help=f'Remote URL to fetch the exercises from (default: {self.remote_url})'
        )

        parser.add_argument(
            '--dont-delete',
            action='store_true',
            dest='skip_delete',
            default=False,
            help='Skips deleting any entries'
        )

    def handle(self, **options):

        remote_url = options['remote_url']

        try:
            val = URLValidator()
            val(remote_url)
            self.remote_url = remote_url
        except ValidationError:
            raise CommandError('Please enter a valid URL')

        self.headers = {
            'User-agent': default_user_agent('wger/{} + requests'.format(get_version()))
        }
        self.sync_categories()
        self.sync_muscles()
        self.sync_equipment()
        self.sync_exercises()
        if not options['skip_delete']:
            self.delete_entries()

    def sync_exercises(self):
        """Synchronize the exercises from the remote server"""

        self.stdout.write('*** Synchronizing exercises...')
        page = 1
        all_exercise_processed = False
        result = requests.get(EXERCISE_API.format(self.remote_url), headers=self.headers).json()
        while not all_exercise_processed:

            for data in result['results']:
                translation_uuid = data['uuid']
                translation_name = data['name']
                translation_description = data['description']
                language_id = data['language']['id']
                license_id = data['license']['id']
                license_author = data['license_author']
                equipment = [Equipment.objects.get(pk=i['id']) for i in data['equipment']]
                muscles = [Muscle.objects.get(pk=i['id']) for i in data['muscles']]
                muscles_sec = [Muscle.objects.get(pk=i['id']) for i in data['muscles_secondary']]

                try:
                    translation = Exercise.objects.get(uuid=translation_uuid)
                    translation.name = translation_name
                    translation.description = translation_description
                    translation.language_id = language_id
                    translation.license_id = license_id
                    translation.license_author = license_author

                    # Note: this should not happen and is an unnecessary workaround
                    #       https://github.com/wger-project/wger/issues/840
                    if not translation.exercise_base:
                        warning = f'Exercise {translation.uuid} has no base, this should not happen!' \
                                  f'Skipping...\n'
                        self.stdout.write(self.style.WARNING(warning))
                        continue
                    translation.exercise_base.category_id = data['category']['id']
                    translation.exercise_base.muscles.set(muscles)
                    translation.exercise_base.muscles_secondary.set(muscles_sec)
                    translation.exercise_base.equipment.set(equipment)
                    translation.exercise_base.save()
                    translation.save()
                except Exercise.DoesNotExist:
                    self.stdout.write(f'Saved new exercise {translation_name}')
                    base = ExerciseBase()
                    base.category_id = data['category']['id']
                    base.save()
                    base.muscles.set(muscles)
                    base.muscles_secondary.set(muscles_sec)
                    base.equipment.set(equipment)
                    base.save()
                    translation = Exercise(
                        uuid=translation_uuid,
                        exercise_base=base,
                        name=translation_name,
                        description=translation_description,
                        language_id=language_id,
                        license_id=data['license']['id'],
                        license_author=license_author,
                    )
                    translation.save()

            if result['next']:
                page += 1
                result = requests.get(result['next'], headers=self.headers).json()
            else:
                all_exercise_processed = True
        self.stdout.write(self.style.SUCCESS('done!\n'))

    def delete_entries(self):
        """Delete exercises that were removed on the server"""

        self.stdout.write('*** Deleting exercises that were removed on the server...')

        page = 1
        all_entries_processed = False
        result = requests.get(DELETION_LOG_API.format(self.remote_url), headers=self.headers).json()
        while not all_entries_processed:
            for data in result['results']:
                uuid = data['uuid']
                model_type = data['model_type']

                if model_type == DeletionLog.MODEL_BASE:
                    try:
                        obj = ExerciseBase.objects.get(uuid=uuid)
                        obj.delete()
                        self.stdout.write(f'Deleted exercise base {uuid}')
                    except ExerciseBase.DoesNotExist:
                        pass

                elif model_type == DeletionLog.MODEL_TRANSLATION:
                    try:
                        obj = Exercise.objects.get(uuid=uuid)
                        obj.delete()
                        self.stdout.write(f"Deleted translation {uuid} ({data['comment']})")
                    except Exercise.DoesNotExist:
                        pass

                elif model_type == DeletionLog.MODEL_IMAGE:
                    try:
                        obj = ExerciseImage.objects.get(uuid=uuid)
                        obj.delete()
                        self.stdout.write(f'Deleted image {uuid}')
                    except ExerciseImage.DoesNotExist:
                        pass

                elif model_type == DeletionLog.MODEL_VIDEO:
                    try:
                        obj = ExerciseVideo.objects.get(uuid=uuid)
                        obj.delete()
                        self.stdout.write(f'Deleted video {uuid}')
                    except ExerciseVideo.DoesNotExist:
                        pass

            if result['next']:
                page += 1
                result = requests.get(result['next'], headers=self.headers).json()
            else:
                all_entries_processed = True
        self.stdout.write(self.style.SUCCESS('done!\n'))

    def sync_equipment(self):
        """Synchronize the equipment from the remote server"""

        self.stdout.write('*** Synchronizing equipment...')
        result = requests.get(EQUIPMENT_API.format(self.remote_url), headers=self.headers).json()
        for equipment_data in result['results']:
            equipment_id = equipment_data['id']
            equipment_name = equipment_data['name']

            try:
                equipment = Equipment.objects.get(pk=equipment_id)
                equipment.name = equipment_name
                equipment.save()
            except Equipment.DoesNotExist:
                self.stdout.write(f'Saved new equipment {equipment_name}')
                equipment = Equipment(id=equipment_id, name=equipment_name)
                equipment.save()
        self.stdout.write(self.style.SUCCESS('done!\n'))

    def sync_muscles(self):
        """Synchronize the muscles from the remote server"""

        self.stdout.write('*** Synchronizing muscles...')
        result = requests.get(MUSCLE_API.format(self.remote_url), headers=self.headers).json()
        for muscle_data in result['results']:
            muscle_id = muscle_data['id']
            muscle_name = muscle_data['name']
            muscle_is_front = muscle_data['is_front']
            muscle_name_en = muscle_data['name_en']
            muscle_url_main = muscle_data['image_url_main']
            muscle_url_secondary = muscle_data['image_url_secondary']

            try:
                muscle = Muscle.objects.get(pk=muscle_id)
                muscle.name = muscle_name
                muscle.is_front = muscle_is_front
                muscle.name_en = muscle_name_en
                muscle.save()
            except Muscle.DoesNotExist:
                muscle = Muscle(
                    id=muscle_id,
                    name=muscle_name,
                    is_front=muscle_is_front,
                    name_en=muscle_name_en
                )
                muscle.save()
                self.stdout.write(
                    self.style.WARNING(
                        f'Saved new muscle {muscle_name}. '
                        f'Save the corresponding images manually'
                    )
                )
                self.stdout.write(self.style.WARNING(muscle_url_main))
                self.stdout.write(self.style.WARNING(muscle_url_secondary))
        self.stdout.write(self.style.SUCCESS('done!\n'))

    def sync_categories(self):
        """Synchronize the categories from the remote server"""

        self.stdout.write('*** Synchronizing categories...')
        result = requests.get(CATEGORY_API.format(self.remote_url), headers=self.headers).json()
        for category_data in result['results']:
            category_id = category_data['id']
            category_name = category_data['name']
            try:
                category = ExerciseCategory.objects.get(pk=category_id)
                category.name = category_name
                category.save()
            except ExerciseCategory.DoesNotExist:
                self.stdout.write(self.style.WARNING(f'Saving new category {category_name}'))
                category = ExerciseCategory(id=category_id, name=category_name)
                category.save()
        self.stdout.write(self.style.SUCCESS('done!\n'))
