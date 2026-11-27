from django import forms
from django.forms import formset_factory


FORMAS_PAGO = [('mensual', 'Mensual'), ('trimestral', 'Trimestral'),
               ('semestral', 'Semestral'), ('anual', 'Anual')]


def opciones(registros, vacio='Selecciona una opción'):
    return [('', vacio)] + [(r['id'], r['nombre']) for r in registros]


class FechaInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, **kwargs):
        super().__init__(format='%Y-%m-%d', **kwargs)


class FiltrosForm(forms.Form):
    asegurado_id = forms.TypedChoiceField(label='Asegurado', required=False, coerce=int, empty_value=None)
    aseguradora_id = forms.TypedChoiceField(label='Aseguradora', required=False, coerce=int, empty_value=None)
    tipo = forms.ChoiceField(label='Tipo de fecha', required=False,
                            choices=[('todos', 'Renovación y cobranza'), ('renovacion', 'Renovación'), ('cobranza', 'Cobranza')])
    desde = forms.DateField(label='Desde', required=False, widget=FechaInput())
    hasta = forms.DateField(label='Hasta', required=False, widget=FechaInput())
    atencion = forms.ChoiceField(label='Atención', required=False,
                                choices=[('todos', 'Todos los casos'), ('requiere', 'Requiere atención'),
                                         ('sin_atencion', 'Sin atención próxima')])

    def __init__(self, *args, asegurados=(), aseguradoras=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asegurado_id'].choices = opciones(asegurados, 'Todos los asegurados')
        self.fields['aseguradora_id'].choices = opciones(aseguradoras, 'Todas las aseguradoras')

    def clean(self):
        datos = super().clean()
        if datos.get('desde') and datos.get('hasta') and datos['desde'] > datos['hasta']:
            self.add_error('hasta', 'La fecha final debe ser igual o posterior a la inicial.')
        datos['tipo'] = datos.get('tipo') or 'todos'
        datos['atencion'] = datos.get('atencion') or 'todos'
        return datos


class AseguradoForm(forms.Form):
    nombre = forms.CharField(label='Nombre del asegurado', max_length=200)


class ContactoForm(forms.Form):
    nombre = forms.CharField(label='Nombre del contacto', required=False, max_length=200)
    rol = forms.ChoiceField(label='Relación', choices=[('general', 'Contacto general'), ('referente', 'Referente')], initial='general')
    telefonos = forms.CharField(label='Teléfonos', required=False, widget=forms.Textarea(attrs={'rows': 2}),
                               help_text='Un teléfono por línea. Conserva el prefijo y los espacios que necesites.')
    correos = forms.CharField(label='Correos electrónicos', required=False, widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Un correo por línea. Los medios de contacto son opcionales.')

    def clean_correos(self):
        correos = [s.strip() for s in self.cleaned_data.get('correos', '').splitlines() if s.strip()]
        campo = forms.EmailField()
        for correo in correos:
            campo.clean(correo)
        return correos

    def clean_telefonos(self):
        return [s.strip() for s in self.cleaned_data.get('telefonos', '').splitlines() if s.strip()]


class AseguradoraForm(forms.Form):
    nombre = forms.CharField(label='Nombre de la aseguradora', max_length=150)


class PolizaForm(forms.Form):
    asegurado_id = forms.TypedChoiceField(label='Asegurado', coerce=int)
    aseguradora_id = forms.TypedChoiceField(label='Aseguradora', coerce=int)
    tipo = forms.ChoiceField(label='Tipo de seguro', choices=[('autos', 'Autos'), ('danios', 'Daños')])
    modalidad_auto = forms.ChoiceField(label='Modalidad de autos', required=False,
                                     choices=[('', 'No aplica a daños'), ('individual', 'Individual'), ('flotilla', 'Flotilla')],
                                     initial='individual')
    numero_poliza = forms.CharField(label='Número de póliza', max_length=100)
    fecha_inicio = forms.DateField(label='Inicio de vigencia', widget=FechaInput())
    fecha_fin = forms.DateField(label='Fin de vigencia / renovación', widget=FechaInput())
    forma_pago = forms.ChoiceField(label='Forma de pago', choices=FORMAS_PAGO)
    tipo_cobro = forms.ChoiceField(label='Tipo de cobro',
                                  choices=[('manual', 'Manual'), ('domiciliado', 'Domiciliado')])
    sin_gracia = forms.BooleanField(label='Sin periodo de gracia', required=False)
    responsable = forms.CharField(label='Responsable', required=False, max_length=150)
    descripcion_riesgo = forms.CharField(label='Bien o riesgo asegurado', required=False,
                                        widget=forms.Textarea(attrs={'rows': 3}))
    aplica_anticipacion = forms.BooleanField(label='Aplicar atención anticipada a esta póliza', required=False, initial=True,
                                           help_text='Usa el plazo de días naturales configurado para renovación y cobranza.')
    vigencia_id = forms.IntegerField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, asegurados=(), aseguradoras=(), modo='crear', **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asegurado_id'].choices = opciones(asegurados)
        self.fields['aseguradora_id'].choices = opciones(aseguradoras)
        if modo != 'crear':
            self.fields['tipo'].disabled = True
            self.fields['modalidad_auto'].disabled = True
            self.fields['asegurado_id'].disabled = True
        if modo == 'renovar':
            for nombre in ('aseguradora_id', 'aplica_anticipacion', 'responsable', 'descripcion_riesgo'):
                self.fields[nombre].disabled = True

    def clean(self):
        datos = super().clean()
        if datos.get('fecha_inicio') and datos.get('fecha_fin') and datos['fecha_fin'] <= datos['fecha_inicio']:
            self.add_error('fecha_fin', 'El fin debe ser posterior al inicio de la vigencia.')
        if datos.get('tipo') == 'danios':
            datos['modalidad_auto'] = ''
            if not datos.get('descripcion_riesgo'):
                self.add_error('descripcion_riesgo', 'Describe el bien o riesgo asegurado.')
        elif datos.get('tipo') == 'autos' and not datos.get('modalidad_auto'):
            self.add_error('modalidad_auto', 'Selecciona individual o flotilla.')
        return datos


class VehiculoForm(forms.Form):
    descripcion = forms.CharField(label='Descripción del vehículo', max_length=250)
    modelo = forms.IntegerField(label='Modelo (año)', min_value=1, max_value=9999)
    serie = forms.CharField(label='Serie', max_length=100)
    inciso = forms.CharField(label='Inciso', max_length=50, required=False,
                             help_text='Obligatorio para cada unidad de una flotilla, por ejemplo: -1.')


class CobroForm(forms.Form):
    id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    fecha_vencimiento = forms.DateField(label='Fecha del cobro', widget=FechaInput())
    estado = forms.ChoiceField(label='Estado', choices=[('pendiente', 'Pendiente'), ('pagado', 'Pagado')], initial='pendiente')


ContactosFormSet = formset_factory(ContactoForm, extra=1, can_delete=True, max_num=100, validate_max=True)
VehiculosFormSet = formset_factory(VehiculoForm, extra=1, can_delete=True, max_num=500, validate_max=True)
CobrosFormSet = formset_factory(CobroForm, extra=1, can_delete=True, max_num=500, validate_max=True)
