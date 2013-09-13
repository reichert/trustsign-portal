# coding=utf-8
import os
from django.contrib.formtools.wizard.views import SessionWizardView
from django.core.files.storage import FileSystemStorage
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect, Http404
from rest_framework.generics import GenericAPIView
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from rest_framework.renderers import UnicodeJSONRenderer, BrowsableAPIRenderer
from portal.certificados import comodo
from portal.certificados.authentication import UserPasswordAuthentication
from portal.certificados.models import Emissao, Voucher
from portal.certificados.serializers import EmissaoNv0Serializer, EmissaoNv1Serializer, EmissaoNv2Serializer, \
    EmissaoNv3Serializer, EmissaoNv4Serializer, EmissaoNvASerializer
from django.conf import settings
from portal.ferramentas.utils import verifica_razaosocial_dominio


class EmissaoAPIView(ListModelMixin, CreateModelMixin, GenericAPIView):
    queryset = Emissao.objects.all()
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer, BrowsableAPIRenderer]

    def get_serializer_context(self):
        """
        Coloca o usuário no kwargs do init do EmissaoSerializer
        """
        context = super(EmissaoAPIView, self).get_serializer_context()
        context.update({
            'user': self.request.user
        })
        return context

    def get_serializer_class(self):
        voucher = self.get_voucher()
        if voucher.ssl_produto in (voucher.PRODUTO_SITE_SEGURO, voucher.PRODUTO_SITE_MONITORADO):
            return EmissaoNv0Serializer
        if voucher.ssl_produto in (voucher.PRODUTO_SSL, voucher.PRODUTO_SSL_WILDCARD):
            return EmissaoNv1Serializer
        if voucher.ssl_produto in (voucher.PRODUTO_SAN_UCC, voucher.PRODUTO_MDC):
            return EmissaoNv2Serializer
        if voucher.ssl_produto == voucher.PRODUTO_EV:
            return EmissaoNv3Serializer
        if voucher.ssl_produto == voucher.PRODUTO_EV_MDC:
            return EmissaoNv4Serializer
        if voucher.ssl_produto in (voucher.PRODUTO_JRE, voucher.PRODUTO_CODE_SIGNING, voucher.PRODUTO_SMIME):
            return EmissaoNvASerializer
        raise Http404()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def get_voucher(self):
        try:
            return Voucher.objects.get(crm_hash=self.request.DATA.get('crm_hash'))
        except Voucher.DoesNotExist:
            raise Http404()


class ReemissaoAPIView(ListModelMixin, CreateModelMixin, GenericAPIView):
    #TODO: implementar
    pass


class RevogacaoAPIView(ListModelMixin, CreateModelMixin, GenericAPIView):
    #TODO: implementar
    pass


class EmailWhoisAPIView(GenericAPIView):
    #TODO: implementar
    pass


class VoucherAPIView(GenericAPIView):
    #TODO: implementar
    pass


class ValidaUrlCSRAPIView(GenericAPIView):
    #TODO: implementar
    pass


class EmissaoBaseWizardView(SessionWizardView):
    # TODO: precisa validar se o self.request.user.username = voucher.cnpj ou usuario esta no grupo com permissao(trust)
    model = None
    done_redirect_url = 'certificado_emitido_sucesso'
    file_storage = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'forms'))
    template_name = 'base.html'
    templates = {}

    # para verificar se precisa de carta de cessao:
    tela_emissao_url = 'tela-1'  # usado para encontrar o form que vai exibir o campo emissao_url
    tela_carta_cessao = 'tela-2' # usado para encontrar o form que vai exibir o campo emissao_carta

    def dispatch(self, request, *args, **kwargs):
        self.instance = self.model()
        self._precisa_carta_cessao = {}
        return super(EmissaoBaseWizardView, self).dispatch(request, *args, **kwargs)

    def post(self, *args, **kwargs):
        self.precisa_carta_cessao(self.steps.current)
        return super(EmissaoBaseWizardView, self).post(*args, **kwargs)

    def get_form_instance(self, step):
        return self.instance

    def done(self, form_list, **kwargs):
        self.save(form_list, **kwargs)
        return HttpResponseRedirect(reverse(self.done_redirect_url))

    def save(self, form_list, **kwargs):
        self.instance.save()

    def get_form_initial(self, step):
        initial = super(EmissaoBaseWizardView, self).get_form_initial(step)
        if step == 'tela-2':
            cd = self.get_cleaned_data_for_step('tela-1')
            initial['emissao_url'] = cd['emissao_url']
            initial['emissao_csr'] = cd['emissao_csr']
        return initial

    def get_form_kwargs(self, step=None):
        kwargs = super(EmissaoBaseWizardView, self).get_form_kwargs(step)
        kwargs['user'] = self.request.user
        kwargs['crm_hash'] = self.kwargs['crm_hash']
        kwargs['precisa_carta_cessao'] = self._precisa_carta_cessao.get(self.steps.current, False)
        return kwargs

    def get_context_data(self, form, **kwargs):
        context = super(EmissaoBaseWizardView, self).get_context_data(form, **kwargs)
        try:
            context.update({
                'voucher': Voucher.objects.get(crm_hash=self.kwargs['crm_hash']),
                'precisa_carta_cessao': self.precisa_carta_cessao(self.steps.current)
            })
        except Voucher.DoesNotExist:
            #raise Http404
            print 'nao encontrou'
        return context

    def get_template_names(self):
        return [self.templates[self.steps.current]]

    def precisa_carta_cessao(self, step):
        if self._precisa_carta_cessao.get(step) is None:
            if self.steps.current == self.tela_carta_cessao:
                cleaned_data = self.get_cleaned_data_for_step(self.tela_emissao_url) or {}

                try:
                    voucher = Voucher.objects.get(crm_hash=self.kwargs.get('crm_hash'))
                    self._precisa_carta_cessao[step] = not verifica_razaosocial_dominio(
                        voucher.cliente_razaosocial,
                        cleaned_data['emissao_url']
                    )
                except Voucher.DoesNotExist:
                    self._precisa_carta_cessao[step] = False

            else:
                self._precisa_carta_cessao[step] = False

        return self._precisa_carta_cessao[step]


class EmissaoNv1WizardView(EmissaoBaseWizardView):
    model = Emissao
    templates = {
        'tela-1': 'certificados/wizard_nv1_1_ssl_wildcard.html',
        'tela-2': 'certificados/wizard_nv1_2_ssl_wildcard.html'
    }

    def save(self, form_list, **kwargs):
        emissao = self.instance
        emissao.solicitante_user_id = self.request.user.pk
        emissao.crm_hash = self.kwargs['crm_hash']
        emissao.voucher = Voucher.objects.get(crm_hash=emissao.crm_hash)

        resposta = comodo.emite_certificado(emissao)
        emissao.comodo_order = resposta['orderNumber']
        emissao.emissao_custo = resposta['totalCost']
        emissao.save()