<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { BookOpen, Send } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { xhsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
const loading = ref(false); const status = ref<any>(null)
const form = reactive({ cookie: '', title: '', content: '', images: '' })
async function refresh() { try { status.value = await xhsApi.status() } catch (e) { ElMessage.error(getErrorMessage(e)) } }
async function login() { if (!form.cookie.trim()) return ElMessage.warning('请输入 Cookie'); loading.value = true; try { await xhsApi.login(form.cookie); form.cookie = ''; await refresh(); ElMessage.success('登录态已保存') } catch (e) { ElMessage.error(getErrorMessage(e)) } finally { loading.value = false } }
async function publish() { if (!form.title || !form.content || !form.images.trim()) return ElMessage.warning('请填写标题、正文和图片路径'); loading.value = true; try { await xhsApi.post({ title: form.title, content: form.content, images: form.images.split('\n').map(x => x.trim()).filter(Boolean) }); ElMessage.success('发布成功') } catch (e) { ElMessage.error(getErrorMessage(e, '发布失败')) } finally { loading.value = false } }
onMounted(refresh)
</script>
<template><div class="page-stack"><section class="page-heading"><div><span class="eyebrow"><BookOpen :size="15" /> CONTENT CHANNEL</span><h1>小红书管理</h1><p>通过 xhs-cli 管理登录态并发布图文笔记</p></div><el-tag :type="status?.connected ? 'success' : 'info'">{{ status?.connected ? '已连接' : '未连接' }}</el-tag></section><section class="panel"><h2>登录小红书</h2><p class="muted">Cookie 仅保存于后端运行环境，请勿粘贴到聊天或日志。</p><el-input v-model="form.cookie" type="password" show-password placeholder="a1=...; web_session=..." /><el-button type="primary" :loading="loading" @click="login">保存登录态</el-button></section><section class="panel"><h2>发布图文笔记</h2><el-input v-model="form.title" maxlength="80" show-word-limit placeholder="标题" /><el-input v-model="form.content" type="textarea" :rows="6" placeholder="正文内容" /><el-input v-model="form.images" type="textarea" :rows="3" placeholder="图片绝对路径，每行一张" /><el-button type="primary" :loading="loading" @click="publish"><Send :size="16" /> 发布笔记</el-button></section></div></template>
