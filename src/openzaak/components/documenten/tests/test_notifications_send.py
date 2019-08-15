import base64
from unittest.mock import patch

from django.test import override_settings

from freezegun import freeze_time
from openzaak.components.documenten.api.tests.utils import get_operation_url
from rest_framework import status
from rest_framework.test import APITestCase
from vng_api_common.constants import VertrouwelijkheidsAanduiding
from vng_api_common.tests import JWTAuthMixin,reverse
from openzaak.components.catalogi.models.tests.factories import InformatieObjectTypeFactory


@freeze_time("2012-01-14")
@override_settings(NOTIFICATIONS_DISABLED=False)
class SendNotifTestCase(JWTAuthMixin, APITestCase):

    @patch('zds_client.Client.from_url')
    def test_send_notif_create_enkelvoudiginformatieobject(self, mock_client):
        """
        Registreer een ENKELVOUDIGINFORMATIEOBJECT
        """
        informatieobjecttype = InformatieObjectTypeFactory.create()
        informatieobjecttype_url = reverse(informatieobjecttype)
        client = mock_client.return_value
        url = get_operation_url('enkelvoudiginformatieobject_create')
        data = {
            'identificatie': 'AMS20180701001',
            'bronorganisatie': '159351741',
            'creatiedatum': '2018-07-01',
            'titel': 'text_extra.txt',
            'auteur': 'ANONIEM',
            'formaat': 'text/plain',
            'taal': 'dut',
            'inhoud': base64.b64encode(b'Extra tekst in bijlage').decode('utf-8'),
            'informatieobjecttype': informatieobjecttype_url,
            'vertrouwelijkheidaanduiding': VertrouwelijkheidsAanduiding.openbaar
        }

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        data = response.json()
        client.create.assert_called_once_with(
            'notificaties',
            {
                'kanaal': 'documenten',
                'hoofdObject': data['url'],
                'resource': 'enkelvoudiginformatieobject',
                'resourceUrl': data['url'],
                'actie': 'create',
                'aanmaakdatum': '2012-01-14T00:00:00Z',
                'kenmerken': {
                    'bronorganisatie': '159351741',
                    'informatieobjecttype': str(informatieobjecttype),
                    'vertrouwelijkheidaanduiding': VertrouwelijkheidsAanduiding.openbaar,
                }
            }
        )
