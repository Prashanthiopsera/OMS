{{- define "oms.name" -}}
oms
{{- end }}

{{- define "oms.fullname" -}}
{{ .Release.Name }}-oms
{{- end }}
