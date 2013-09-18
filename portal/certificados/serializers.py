from rest_framework.fields import Field, DateTimeField
from rest_framework.serializers import ModelSerializer, ValidationError
from portal.certificados.models import Emissao, Voucher
from portal.certificados.validations import ValidateEmissaoUrlMixin, ValidateEmissaoCSRMixin, \
    ValidateEmissaoValidacaoEmail, ValidateEmissaoPrimaryDN
from portal.ferramentas.utils import decode_csr


class VoucherSerializer(ModelSerializer):

    order_date = DateTimeField(format='%d/%m/%Y %H:%M')

    class Meta:
        model = Voucher
        exclude = ['order_item_value']


class EmissaoModelSerializer(ModelSerializer):
    REQUIRED_FIELDS = ()
    validacao_manual = False
    _csr_decoded = None
    _voucher = None
    user = None
    ValidationError = ValidationError

    def __init__(self, user=None, crm_hash=None, **kwargs):
        self.user = user
        self._crm_hash = crm_hash
        super(EmissaoModelSerializer, self).__init__(**kwargs)

    def get_csr_decoded(self, valor):
        if not self._csr_decoded:
            self._csr_decoded = decode_csr(valor)
        return self._csr_decoded

    def get_fields(self):
        fields = super(EmissaoModelSerializer, self).get_fields()
        for f in self.REQUIRED_FIELDS:
            fields[f].required = True
        return fields

    def get_voucher(self):
        if not self._voucher:
            self._voucher = Voucher.objects.get(crm_hash=self._crm_hash)
        return self._voucher


class EmissaoNv0Serializer(EmissaoModelSerializer, ValidateEmissaoUrlMixin):
    REQUIRED_FIELDS = ('emission_url', )

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_url', 'emission_assignment_letter')


class EmissaoNv1Serializer(EmissaoModelSerializer, ValidateEmissaoUrlMixin, ValidateEmissaoCSRMixin,
                           ValidateEmissaoValidacaoEmail):
    REQUIRED_FIELDS = ('emission_url', 'emission_dcv_emails', 'emission_publickey_sendto',
                       'emission_server_type', 'emission_csr')

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_url', 'emission_dcv_emails', 'emission_publickey_sendto',
                  'emission_server_type', 'emission_csr', 'emission_assignment_letter')


class EmissaoNv2Serializer(EmissaoModelSerializer, ValidateEmissaoUrlMixin, ValidateEmissaoCSRMixin,
                           ValidateEmissaoPrimaryDN):
    REQUIRED_FIELDS = ('emission_url', 'emission_dcv_emails', 'emission_publickey_sendto',
                       'emission_server_type', 'emission_csr', 'emission_primary_dn', )

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_url', 'emission_csr', 'emission_primary_dn', 'emission_dcv_emails',
                  'emission_publickey_sendto', 'emission_server_type', 'emission_assignment_letter')


class EmissaoNv3Serializer(EmissaoModelSerializer, ValidateEmissaoUrlMixin, ValidateEmissaoCSRMixin):
    validacao_manual = True

    REQUIRED_FIELDS = ('emission_url', 'emission_dcv_emails', 'emission_publickey_sendto',
                       'emission_server_type', 'emission_csr', 'emission_articles_of_incorporation',
                       'emission_address_proof', 'emission_ccsa', 'emission_evcr')

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_url', 'emission_publickey_sendto', 'emission_dcv_emails',
                  'emission_server_type', 'emission_csr', 'emission_assignment_letter',
                  'emission_articles_of_incorporation', 'emission_address_proof', 'emission_ccsa', 'emission_evcr')


class EmissaoNv4Serializer(EmissaoModelSerializer, ValidateEmissaoCSRMixin, ValidateEmissaoPrimaryDN):
    REQUIRED_FIELDS = ('emission_url', 'emission_dcv_emails', 'emission_publickey_sendto',
                       'emission_server_type', 'emission_csr', 'emission_articles_of_incorporation',
                       'emission_address_proof', 'emission_ccsa', 'emission_evcr', 'emission_primary_dn')

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_url', 'emission_fqdns', 'emission_publickey_sendto',
                  'emission_dcv_emails', 'emission_server_type', 'emission_csr', 'emission_primary_dn',
                  'emission_assignment_letter', 'emission_articles_of_incorporation', 'emission_address_proof',
                  'emission_ccsa', 'emission_evcr')


class EmissaoNvASerializer(EmissaoModelSerializer):
    REQUIRED_FIELDS = ('emission_address_proof',)

    class Meta:
        model = Emissao
        fields = ('crm_hash', 'emission_address_proof',)