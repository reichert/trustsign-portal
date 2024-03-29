# coding=utf-8
from StringIO import StringIO
from django.http import HttpResponse
from mezzanine.pages.page_processors import processor_for
from portal.ferramentas.forms import SSLCheckerForm, CSRDecoderForm, CertificateKeyMatcherForm, SSLConverterForm
from portal.ferramentas.models import FerramentasPage


@processor_for(FerramentasPage)
def ferramentas_processor(request, page):

    ssl_checker_form = SSLCheckerForm()
    csr_decoder_form = CSRDecoderForm()
    certificate_key_matcher_form = CertificateKeyMatcherForm()
    ssl_converter_form = SSLConverterForm()
    resultado = ''

    if request.method == 'POST':
        op = request.POST.get('operation')
        if op == 'ssl-checker':
            ssl_checker_form = SSLCheckerForm(request.POST)
            if ssl_checker_form.is_valid():
                resultado = ssl_checker_form.processa()
        elif op == 'csr-decoder':
            csr_decoder_form = CSRDecoderForm(request.POST)
            if csr_decoder_form.is_valid():
                try:
                    resultado = csr_decoder_form.processa()
                except:
                    resultado = {
                        'ok': False
                    }

        elif op == 'certificate-key-matcher':
            certificate_key_matcher_form = CertificateKeyMatcherForm(request.POST, request.FILES)
            if certificate_key_matcher_form.is_valid():
                try:
                    resultado = certificate_key_matcher_form.processa()
                except Exception:
                    resultado = {
                        'ok': False
                    }
        elif op == 'ssl-converter':
            nomes = {
                SSLConverterForm.STANDARD_PEM: ('%s.cer', 'application/octet-stream'),
                SSLConverterForm.DER_BINARY: ('%s.der', 'application/octet-stream'),
                SSLConverterForm.P7B_PKCS_7: ('%s.p7b', 'application/x-pkcs7-certificates'),
                SSLConverterForm.PFX_PKCS_12: ('%s.p12', 'application/x-pkcs12'),
            }

            ssl_converter_form = SSLConverterForm(request.POST, request.FILES)
            if ssl_converter_form.is_valid():
                certificado = ssl_converter_form.processa()
                tipo = int(ssl_converter_form.cleaned_data['tipo_para_converter'])

                nome = '.'.join(ssl_converter_form.cleaned_data['certificado'].name.split('.')[:-1])

                response = HttpResponse(StringIO(certificado), content_type=nomes[tipo][1])
                response['Content-Disposition'] = 'attachment; filename=%s' % nomes[tipo][0] % nome

                return response
    else:
        op = ''

    return {
        'op': op,
        'resultado': resultado,
        'form_ssl_checker': ssl_checker_form,
        'form_csr_decoder': csr_decoder_form,
        'form_certificate_key_matcher': certificate_key_matcher_form,
        'form_ssl_converter': ssl_converter_form,
    }