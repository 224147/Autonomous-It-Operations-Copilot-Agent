{{- define "copilot.fullname" -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{- define "copilot.labels" -}}
app.kubernetes.io/name: autonomous-it-ops-copilot
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "copilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (printf "%s-backend" (include "copilot.fullname" .)) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "copilot.secretName" -}}
{{- if .Values.secrets.create -}}{{ include "copilot.fullname" . }}-secrets{{- else -}}{{ .Values.existingSecret }}{{- end -}}
{{- end -}}

{{- define "copilot.backendImage" -}}
{{ .Values.backend.image.repository }}:{{ default .Chart.AppVersion .Values.backend.image.tag }}
{{- end -}}

{{- define "copilot.frontendImage" -}}
{{ .Values.frontend.image.repository }}:{{ default .Chart.AppVersion .Values.frontend.image.tag }}
{{- end -}}

{{- define "copilot.podSecurity" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "copilot.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
