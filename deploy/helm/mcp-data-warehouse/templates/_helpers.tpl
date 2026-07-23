{{- define "mcp-data-warehouse.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-data-warehouse.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "mcp-data-warehouse.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{ include "mcp-data-warehouse.selectorLabels" . }}
{{- end -}}

{{- define "mcp-data-warehouse.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mcp-data-warehouse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
