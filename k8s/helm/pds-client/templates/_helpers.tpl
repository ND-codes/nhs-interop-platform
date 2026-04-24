{{/* Common name + label helpers shared across templates. */}}

{{- define "pds-client.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "pds-client.fullname" -}}
{{- default (include "pds-client.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "pds-client.labels" -}}
app.kubernetes.io/name: {{ include "pds-client.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: nhs-interop
{{- end -}}

{{- define "pds-client.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pds-client.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
