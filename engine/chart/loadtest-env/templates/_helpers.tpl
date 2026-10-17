{{- define "loadtest-env.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
loadtest.platform/run-id: {{ .Values.runId | quote }}
{{- end }}

{{/* Pod-level security context satisfying Pod Security "restricted". Arg: uid (int) */}}
{{- define "loadtest-env.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ .uid }}
runAsGroup: {{ .uid }}
fsGroup: {{ .uid }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "loadtest-env.containerSecurity" -}}
runAsNonRoot: true
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* Arg: dict with cpu, memory. requests == limits (Guaranteed QoS). */}}
{{- define "loadtest-env.resources" -}}
requests:
  cpu: {{ .cpu | quote }}
  memory: {{ .memory | quote }}
limits:
  cpu: {{ .cpu | quote }}
  memory: {{ .memory | quote }}
{{- end }}

{{/* nodeSelector + tolerations on every Deployment. Arg: root context */}}
{{- define "loadtest-env.scheduling" -}}
{{- with .Values.scheduling.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.scheduling.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
