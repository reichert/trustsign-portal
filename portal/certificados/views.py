# -*- coding: utf-8 -*-
import os
from django.contrib.formtools.wizard.views import SessionWizardView
from django.core.exceptions import PermissionDenied
from django.core.files.storage import FileSystemStorage
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponseRedirect, Http404
from django.utils.timezone import now
from django.views.generic import ListView, CreateView, UpdateView, TemplateView
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.mixins import CreateModelMixin, RetrieveModelMixin
from rest_framework.renderers import UnicodeJSONRenderer
from rest_framework.response import Response
from portal.certificados import comodo, erros
from portal.certificados.authentication import UserPasswordAuthentication
from portal.certificados.forms import RevogacaoForm, ReemissaoForm
from portal.certificados.models import Emissao, Voucher, Revogacao
from portal.certificados.serializers import EmissaoNv0Serializer, EmissaoNv1Serializer, EmissaoNv2Serializer, \
    EmissaoNv3Serializer, EmissaoNv4Serializer, EmissaoNvASerializer, VoucherSerializer, RevogacaoSerializer, \
    ReemissaoSerializer, EmissaoValidaSerializer, EmissaoNvBSerializer
from django.conf import settings
import logging

log = logging.getLogger('portal.certificados.view')


def erro_rest(*lista_erros):
    """
    Cria o Response com o padrão:

    {
        'errors': [ {'code':X, 'message': Y}, {'code':A, 'message': B},]
    }

    onde a chamada seria erro_rest((X,Y), (A,B))
    """
    return Response({
        'errors': [{'code': e[0], 'message': e[1]} for e in lista_erros]
    }, status=status.HTTP_400_BAD_REQUEST)


def atualiza_voucher(voucher, dados_voucher):
        v = dados_voucher.get('callback_tratamento')
        if v:
            voucher.customer_callback_title = v

        v = dados_voucher.get('callback_nome')
        if v:
            voucher.customer_callback_firstname = v

        v = dados_voucher.get('callback_sobrenome')
        if v:
            voucher.customer_callback_lastname = v

        v = dados_voucher.get('callback_email')
        if v:
            voucher.customer_callback_email = v

        v = dados_voucher.get('callback_telefone')
        if v:
            voucher.customer_callback_phone = v

        v = dados_voucher.get('callback_observacao')
        if v:
            voucher.customer_callback_note = v

        v = dados_voucher.get('callback_username')
        if v:
            voucher.customer_callback_username = v

        v = dados_voucher.get('callback_password')
        if v:
            voucher.customer_callback_password = v


class AddErrorResponseMixin(object):
    """
    Mixin que adiciona o metodo error_response, que retorna um Response, com o padrão de erros apartir do serializer.errors:

    {
        'errors': [ {'code':X, 'message': Y}, {'code':A, 'message': B}, ...]
    }

    """

    def error_response(self, serializer):
        return erro_rest(*[('XX', '%s: %s' % (campo, erro))
                           for campo, lista_erros in serializer.errors.iteritems()
                           for erro in lista_erros])


class EmissaoAPIView(CreateModelMixin, AddErrorResponseMixin, GenericAPIView):
    queryset = Emissao.objects.all()
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]
    _voucher = None

    def get_serializer(self, instance=None, data=None,
                       files=None, many=False, partial=False):
        """
        Return the serializer instance that should be used for validating and
        deserializing input, and for serializing output.
        """
        serializer_class = self.get_serializer_class()
        context = self.get_serializer_context()
        return serializer_class(instance=instance, data=data, files=files,
                                many=many, partial=partial, context=context, user=self.request.user,
                                crm_hash=self.request.DATA.get('crm_hash'))

    def get_serializer_class(self):
        voucher = self.get_voucher()
        if voucher.ssl_product in (voucher.PRODUTO_SITE_SEGURO, voucher.PRODUTO_SITE_MONITORADO):
            return EmissaoNv0Serializer
        if voucher.ssl_product in (voucher.PRODUTO_SSL, voucher.PRODUTO_SSL_WILDCARD):
            return EmissaoNv1Serializer
        if voucher.ssl_product in (voucher.PRODUTO_SAN_UCC, voucher.PRODUTO_MDC):
            return EmissaoNv2Serializer
        if voucher.ssl_product == voucher.PRODUTO_EV:
            return EmissaoNv3Serializer
        if voucher.ssl_product == voucher.PRODUTO_EV_MDC:
            return EmissaoNv4Serializer
        if voucher.ssl_product in (voucher.PRODUTO_JRE, voucher.PRODUTO_CODE_SIGNING):
            return EmissaoNvASerializer
        if voucher.ssl_product == voucher.PRODUTO_SMIME:
            return EmissaoNvBSerializer
        log.warning('voucher #%s com produto inválido' % voucher.crm_hash)
        raise Http404()

    def post(self, request, *args, **kwargs):
        try:
            voucher = self.get_voucher()
        except Voucher.DoesNotExist:
            return erro_rest((erros.ERRO_VOUCHER_NAO_ENCONTRADO,
                              erros.get_erro_message(erros.ERRO_VOUCHER_NAO_ENCONTRADO)))

        log.info('request DATA: %s ' % unicode(request.DATA))
        log.info('request FILES: %s ' % unicode(request.FILES))
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():

            emissao = serializer.object
            emissao.requestor_user_id = self.request.user.pk
            emissao.crm_hash = request.DATA.get('crm_hash')
            emissao.emission_fqdns = ' '.join(serializer.get_csr_decoded(emissao.emission_csr).get('dnsNames', ''))

            emissao.voucher = voucher

            # self.atualiza_voucher(voucher) TODO: TBD > o que fazer com os dados do voucher
            # TODO: substituir o if abaixo após tests com TQI
            # if serializer.validacao_manual:
            if False:
                emissao.emission_status = emissao.STATUS_EMISSAO_APROVACAO_PENDENTE
            else:
                emissao.emission_status = emissao.STATUS_EMISSAO_ENVIO_COMODO_PENDENTE

            self.pre_save(serializer.object)
            self.object = serializer.save(force_insert=True)
            self.post_save(self.object, created=True)
            return Response({}, status=status.HTTP_200_OK)

        return self.error_response(serializer)

    def get_voucher(self):
        if not self._voucher:
            self._voucher = Voucher.objects.get(crm_hash=self.request.DATA.get('crm_hash'), emissao__isnull=True)
        return self._voucher


class ReemissaoAPIView(CreateModelMixin, AddErrorResponseMixin, GenericAPIView):
    model = Emissao
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]
    serializer_class = ReemissaoSerializer

    def post(self, request, *args, **kwargs):
        try:
            emissao = Emissao.objects.get(crm_hash=request.DATA.get('crm_hash'))
        except Emissao.DoesNotExist:
            return erro_rest(('---', u'Emissão não encontrada'))  # TODO: corrigir codigo erro

        if emissao.emission_status not in (Emissao.STATUS_EMITIDO, Emissao.STATUS_REEMITIDO):
            return erro_rest(('---', u'Emissão com status: %s' % emissao.get_emission_status_display()))  # TODO: corrigir codigo erro

        serializer = self.get_serializer(data=request.DATA, files=request.FILES, instance=emissao)

        if serializer.is_valid():
            emissao = serializer.object

            emissao.emission_status = Emissao.STATUS_REEMISSAO_ENVIO_COMODO_PENDENTE
            emissao.save()

            if not settings.COMODO_ENVIAR_COMO_TESTE:
                self.pre_save(serializer.object)
                self.object = serializer.save(force_insert=True)
                self.post_save(self.object, created=True)

            headers = self.get_success_headers(serializer.data)
            return Response({}, status=status.HTTP_200_OK, headers=headers)

        return self.error_response(serializer)


class RevogacaoAPIView(CreateModelMixin, AddErrorResponseMixin, GenericAPIView):
    model = Revogacao
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]
    serializer_class = RevogacaoSerializer

    def post(self, request, *args, **kwargs):
        try:
            emissao = Emissao.objects.select_related('voucher').get(crm_hash=self.request.DATA.get('crm_hash'))
        except Emissao.DoesNotExist:
            return erro_rest(('---', u'Emissão não encontrada'))  # TODO corrigir codigo erro

        if emissao.emission_status not in (Emissao.STATUS_EMITIDO, Emissao.STATUS_REEMITIDO):
            return erro_rest(('---', u'Emissão com status: %s' % emissao.get_emission_status_display()))  # TODO: corrigir codigo erro

        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():
            revogacao = serializer.object
            revogacao.emission = emissao

            emissao.status = Emissao.STATUS_REVOGACAO_APROVACAO_PENDENTE
            emissao.save()

            self.pre_save(serializer.object)
            self.object = serializer.save(force_insert=True)
            self.post_save(self.object, created=True)

            emissao.voucher.ssl_revoked_date = now()
            emissao.voucher.save()

            headers = self.get_success_headers(serializer.data)
            return Response({}, status=status.HTTP_200_OK, headers=headers)

        return self.error_response(serializer)


class EmailWhoisAPIView(GenericAPIView):
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]

    def get(self, request, *args, **kwargs):
        try:
            url = request.GET.get('emission_url')
            if not url:
                return erro_rest(('1321', 'Campo emission_url requerido'))  # TODO: codigo erro
            return Response({
                'email_list': comodo.get_emails_validacao(url)
            })
        except Exception:
            return erro_rest(('-1', 'Erro interno do servidor'))


class VoucherCreateAPIView(CreateModelMixin, AddErrorResponseMixin, GenericAPIView):
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]
    serializer_class = VoucherSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():
            self.pre_save(serializer.object)
            self.object = serializer.save(force_insert=True)
            self.post_save(self.object, created=True)
            headers = self.get_success_headers(serializer.data)
            return Response({}, status=status.HTTP_200_OK,
                            headers=headers)

        return self.error_response(serializer)


class VoucherAPIView(RetrieveModelMixin, GenericAPIView):
    authentication_classes = [UserPasswordAuthentication]
    renderer_classes = [UnicodeJSONRenderer]
    serializer_class = VoucherSerializer

    def get(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Voucher.DoesNotExist:
            return erro_rest(('---', 'Voucher não encontrado'))  # TODO: corrigir codigo erro
        serializer = self.get_serializer(self.object)
        data = serializer.data
        voucher = self.object

        novo = {'contact': {}, 'customer': {}, 'product': {}, 'order': {}}
        for k, v in data.iteritems():
            if k.startswith('customer_callback'):
                novo['contact'][k] = v
            elif k.startswith('customer'):
                novo['customer'][k] = v
            elif k.startswith('ssl'):
                novo['product'][k] = v
            elif k.startswith('order'):
                novo['order'][k] = v
            else:
                novo[k] = v

        novo['product']['ssl_line'] = voucher.get_ssl_line_display()
        novo['product']['ssl_term'] = voucher.get_ssl_term_display()
        novo['product']['ssl_product'] = voucher.get_ssl_product_display()

        if voucher.ssl_product == Voucher.PRODUTO_SMIME:
            novo['product']['ssl_password'] = '********'

        if voucher.ssl_product in (Voucher.PRODUTO_CODE_SIGNING, Voucher.PRODUTO_JRE):
            novo['product']['ssl_username'] = voucher.ssl_username
            novo['product']['ssl_password'] = '********'

        try:
            emissao = self.object.emissao
            novo['status_code'] = emissao.emission_status
            novo['status_text'] = emissao.get_emission_status_display()
            novo['product']['ssl_url'] = emissao.emission_url
            if emissao.emission_fqdns:
                novo['product']['ssl_urls'] = emissao.emission_fqdns.split(' ')
            if emissao.emitido:
                novo['product']['ssl_valid_from'] = voucher.ssl_valid_from
                novo['product']['ssl_valid_to'] = voucher.ssl_valid_to
                novo['product']['ssl_seal_html'] = voucher.ssl_seal_html
                novo['product']['ssl_publickey'] = voucher.ssl_publickey
        except Emissao.DoesNotExist:
            novo['status_code'] = Emissao.STATUS_NAO_EMITIDO
            novo['status_text'] = dict(Emissao.STATUS_CHOICES)[Emissao.STATUS_NAO_EMITIDO]

        return Response(novo)

    def get_object(self, queryset=None):
        obj = Voucher.objects.get(crm_hash=self.request.GET.get('crm_hash'))

        # May raise a permission denied
        self.check_object_permissions(self.request, obj)

        return obj


class ValidaUrlCSRAPIView(EmissaoAPIView):
    required_fields = None

    def post(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.DATA, files=request.FILES)

            if serializer.is_valid():
                required_fields = self.required_fields
                if serializer.validacao_carta_cessao_necessaria:
                    required_fields.append('emission_assignment_letter')

                data = {
                    'required_fields': required_fields,
                    'server_list': Emissao.SERVIDOR_TIPO_CHOICES,
                }

                emissao = serializer.object
                csr = serializer.get_csr_decoded(emissao.emission_csr)

                if 'dnsNames' in csr and csr.get('dnsNames'):
                    data['ssl_urls'] = [{'url': dominio,
                                         'emission_dcv_emails': comodo.get_emails_validacao_padrao(dominio),
                                         'primary': dominio == emissao.emission_url} for dominio in csr['dnsNames']]
                else:
                    data['ssl_urls'] = [{'url': emissao.emission_url,
                                         'emission_dcv_emails': comodo.get_emails_validacao_padrao(emissao.emission_url),
                                         'primary': True}]
                return Response(data, status=status.HTTP_200_OK)
        except Voucher.DoesNotExist:
            return erro_rest((erros.ERRO_VOUCHER_NAO_ENCONTRADO,
                              erros.get_erro_message(erros.ERRO_VOUCHER_NAO_ENCONTRADO)))

        return self.error_response(serializer)

    def get_serializer_class(self):
        klass = super(ValidaUrlCSRAPIView, self).get_serializer_class()
        self.required_fields = klass.REQUIRED_FIELDS
        return EmissaoValidaSerializer


class EscolhaVoucherView(ListView):
    template_name = 'certificados/escolha_voucher.html'
    model = Voucher
    context_object_name = 'vouchers'

    def get_queryset(self):
        qs = Voucher.objects.select_related('emissao').filter(
            Q(emissao__isnull=True) | ~Q(emissao__emission_status=Emissao.STATUS_REVOGADO))
        user = self.request.user
        if not user.is_superuser and user.get_profile().is_cliente:
            qs = qs.filter(customer_cnpj=user.username)
        return qs


class VouchersPendentesListView(ListView):
    template_name = 'certificados/vouchers_pentendes.html'
    model = Voucher
    context_object_name = 'vouchers'

    def get_queryset(self):
        user = self.request.user
        profile = user.get_profile()
        if profile.perfil != profile.PERFIL_TRUSTSIGN and not user.is_superuser:
            raise PermissionDenied
        return Voucher.objects.select_related('emissao').filter(
            emissao__emission_status__in=(
                Emissao.STATUS_EMISSAO_APROVACAO_PENDENTE, Emissao.STATUS_REVOGACAO_APROVACAO_PENDENTE
            ))


class EmissaoWizardView(SessionWizardView):
    model = Emissao
    done_redirect_url = 'certificado_emitido_sucesso'
    file_storage = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'forms'))
    template_name = 'base.html'
    templates = {}
    _voucher = None
    revisao = False

    # para verificar se precisa de carta de cessao:
    tela_emissao_url = 'tela-1'  # usado para encontrar o form que vai exibir o campo emission_url
    tela_carta_cessao = 'tela-2'  # usado para encontrar o form que vai exibir o campo emission_assignment_letter

    produtos_voucher = ()

    def dispatch(self, request, *args, **kwargs):
        if self.revisao:
            self.instance = Emissao.objects.get(crm_hash=self.kwargs['crm_hash'])
        else:
            if Emissao.objects.filter(crm_hash=self.kwargs.get('crm_hash')).exists():
                raise Http404()  # Não é possível emitir duas vezes o mesmo voucher
            self.instance = self.model()
        voucher = self.get_voucher()
        if voucher.ssl_product not in self.produtos_voucher:
            raise Http404()
        user = self.request.user
        if (voucher.customer_cnpj != user.username or user.get_profile().is_trustsign) and not user.is_superuser:
            raise PermissionDenied()

        return super(EmissaoWizardView, self).dispatch(request, *args, **kwargs)

    def post(self, *args, **kwargs):
        return super(EmissaoWizardView, self).post(*args, **kwargs)

    def get_form_instance(self, step):
        return self.instance

    def done(self, form_list, **kwargs):
        self.save(form_list, **kwargs)
        return HttpResponseRedirect(reverse(self.done_redirect_url))

    def get_form_initial(self, step):
        initial = super(EmissaoWizardView, self).get_form_initial(step)

        if step == 'tela-1':
            voucher = self.get_voucher()
            initial.update({
                'callback_tratamento': voucher.customer_callback_title,
                'callback_nome': voucher.customer_callback_firstname,
                'callback_sobrenome': voucher.customer_callback_lastname,
                'callback_email': voucher.customer_callback_email,
                'callback_telefone': voucher.customer_callback_phone,
                'callback_observacao': voucher.customer_callback_note,
            })

        elif step == 'tela-2' and not self.revisao:
            cd = self.get_cleaned_data_for_step('tela-1')
            initial['emission_url'] = cd['emission_url']
            initial['emission_csr'] = cd['emission_csr']

        return initial

    def get_form_kwargs(self, step=None):
        kwargs = super(EmissaoWizardView, self).get_form_kwargs(step)

        kwargs.update({
            'user': self.request.user,
            'crm_hash': self.kwargs['crm_hash'],
            'voucher': self.get_voucher(),
        })

        return kwargs

    def get_context_data(self, form, **kwargs):
        context = super(EmissaoWizardView, self).get_context_data(form, **kwargs)

        context.update({
            'voucher': self.get_voucher(),
            'emissao': self.instance,
        })

        if self.steps.current == 'tela-confirmacao':
            context['dados_form1'] = self.get_cleaned_data_for_step('tela-1')
            if 'tela-2' in self.templates:
                context['dados_form2'] = self.get_cleaned_data_for_step('tela-2')

        return context

    def get_template_names(self):
        return [self.templates[self.steps.current]]

    def get_voucher(self):
        if not self._voucher:
            try:
                self._voucher = Voucher.objects.get(crm_hash=self.kwargs.get('crm_hash'))
            except Voucher.DoesNotExist:
                raise Http404()

        return self._voucher

    def save(self, form_list, **kwargs):
        emissao = self.instance
        if not self.revisao:
            emissao.requestor_user_id = self.request.user.pk
            emissao.crm_hash = self.kwargs['crm_hash']
            emissao.voucher = self.get_voucher()

        atualiza_voucher(emissao.voucher, dados_voucher=self.get_cleaned_data_for_step('tela-1'))

        if self.revisao or any(f.validacao_manual for f in form_list):
            emissao.emission_status = emissao.STATUS_EMISSAO_APROVACAO_PENDENTE
            if self.revisao:
                emissao.emission_reviewer = self.request.user
        else:
            emissao.emission_status = emissao.STATUS_EMISSAO_ENVIO_COMODO_PENDENTE

        emissao.save()


class EmissaoNv1WizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_SSL, Voucher.PRODUTO_SSL_WILDCARD)
    templates = {
        'tela-1': 'certificados/nv1/wizard_tela_1.html',
        'tela-2': 'certificados/nv1/wizard_tela_2.html',
        'tela-confirmacao': 'certificados/nv1/wizard_tela_3_confirmacao.html'
    }


class EmissaoNv2WizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_SAN_UCC, Voucher.PRODUTO_MDC)
    templates = {
        'tela-1': 'certificados/nv2/wizard_tela_1.html',
        'tela-2': 'certificados/nv2/wizard_tela_2.html',
        'tela-confirmacao': 'certificados/nv2/wizard_tela_3_confirmacao.html'
    }


class EmissaoNv3WizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_EV,)
    templates = {
        'tela-1': 'certificados/nv3/wizard_tela_1.html',
        'tela-2': 'certificados/nv3/wizard_tela_2.html',
        'tela-confirmacao': 'certificados/nv3/wizard_tela_3_confirmacao.html'
    }


class EmissaoNv4WizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_EV_MDC,)
    templates = {
        'tela-1': 'certificados/nv4/wizard_tela_1.html',
        'tela-2': 'certificados/nv4/wizard_tela_2.html',
        'tela-confirmacao': 'certificados/nv4/wizard_tela_3_confirmacao.html'
    }


class EmissaoNvAWizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_JRE, Voucher.PRODUTO_CODE_SIGNING)
    templates = {
        'tela-1': 'certificados/nvA/wizard_tela_1.html',
        'tela-confirmacao': 'certificados/nvA/wizard_tela_2_confirmacao.html'
    }


class EmissaoNvBWizardView(EmissaoWizardView):
    produtos_voucher = (Voucher.PRODUTO_SMIME,)
    templates = {
        'tela-1': 'certificados/nvB/wizard_tela_1.html',
        'tela-confirmacao': 'certificados/nvB/wizard_tela_2_confirmacao.html'
    }


class RevogacaoView(CreateView):
    form_class = RevogacaoForm
    template_name = 'certificados/revogacao.html'
    _voucher = None

    def get_context_data(self, **kwargs):
        context = super(RevogacaoView, self).get_context_data(**kwargs)
        context.update({
            'voucher': self.get_voucher()
        })
        return context

    def get_crm_hash(self):
        return self.kwargs.get('crm_hash')

    def get_voucher(self):
        if not self._voucher:
            try:
                # só é para retornar voucher que já foram emitidos
                self._voucher = Voucher.objects.select_related('emissao').get(crm_hash=self.get_crm_hash(),
                                                                              emissao__isnull=False)
            except Voucher.DoesNotExist:
                raise Http404()
        return self._voucher

    def form_valid(self, form):
        voucher = self.get_voucher()
        revogacao = self.object = form.save(commit=False)
        emissao = voucher.emissao

        revogacao.crm_hash = self.get_crm_hash()
        revogacao.emission = emissao
        revogacao.save()

        emissao.emission_status = emissao.STATUS_REVOGACAO_APROVACAO_PENDENTE
        emissao.save()

        return HttpResponseRedirect(self.get_success_url())


class ReemissaoView(UpdateView):
    form_class = ReemissaoForm
    template_name = 'certificados/reemissao.html'
    _voucher = None

    def get_context_data(self, **kwargs):
        context = super(ReemissaoView, self).get_context_data(**kwargs)
        context.update({
            'voucher': self.get_voucher()
        })
        return context

    def get_object(self, queryset=None):
        try:
            return Emissao.objects.get(crm_hash=self.get_crm_hash())
        except Emissao.DoesNotExist:
            raise Http404

    def get_initial(self):
        initial = super(ReemissaoView, self).get_initial()
        voucher = self.get_voucher()
        initial.update({
            'callback_tratamento': voucher.customer_callback_title,
            'callback_nome': voucher.customer_callback_firstname,
            'callback_sobrenome': voucher.customer_callback_lastname,
            'callback_email': voucher.customer_callback_email,
            'callback_telefone': voucher.customer_callback_phone,
            'callback_observacao': voucher.customer_callback_note,
            'callback_username': voucher.customer_callback_username,
        })
        return initial

    def get_crm_hash(self):
        return self.kwargs.get('crm_hash')

    def get_voucher(self):
        if not self._voucher:
            try:
                # só é para retornar voucher que já foram emitidos
                self._voucher = Voucher.objects.select_related('emissao').filter(emissao__isnull=False).get(
                    crm_hash=self.get_crm_hash())
            except Voucher.DoesNotExist:
                raise Http404()
        return self._voucher

    def form_valid(self, form):
        emissao = self.object = form.save(commit=False)

        emissao.save()

        voucher = self.get_voucher()
        atualiza_voucher(voucher, form.cleaned_data)
        voucher.save()
        return HttpResponseRedirect(self.get_success_url())


class RevisaoEmissaoNv1WizardView(EmissaoNv1WizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class RevisaoEmissaoNv2WizardView(EmissaoNv2WizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class RevisaoEmissaoNv3WizardView(EmissaoNv3WizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class RevisaoEmissaoNv4WizardView(EmissaoNv4WizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class RevisaoEmissaoNvAWizardView(EmissaoNvAWizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class RevisaoEmissaoNvBWizardView(EmissaoNvBWizardView):
    revisao = True
    done_redirect_url = 'voucher-pendentes-lista'


class AprovaVoucherPendenteView(TemplateView):

    def render_to_response(self, context, **response_kwargs):
        user = self.request.user
        if not user.get_profile().is_trustsign and not user.is_superuser:
            raise PermissionDenied

        try:
            emissao = Emissao.objects.select_related('voucher').get(crm_hash=self.kwargs['crm_hash'])
        except Emissao.DoesNotExist:
            raise Http404

        emissao.aprova(self.request.user)

        emissao.save()

        return HttpResponseRedirect(reverse('voucher-pendentes-lista'))