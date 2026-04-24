{{/* Common name + label helpers shared across templates. */}}

{{- define "transform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "transform.fullname" -}}
{{- default (include "transform.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "transform.labels" -}}
app.kubernetes.io/name: {{ include "transform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: nhs-interop
{{- end -}}

{{- define "transform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "transform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
