from __future__ import annotations
import asyncio, json, os, shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.api.deps import CurrentAdmin
router = APIRouter(prefix='/xhs', tags=['Xiaohongshu'])
XHS_HOME = Path(os.getenv('XHS_CLI_HOME', '/tmp/xsentinel-xhs'))
class LoginPayload(BaseModel): cookie: str = Field(min_length=10)
class PostPayload(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=20000)
    images: list[str] = Field(min_length=1, max_length=18)
async def _run(*args: str):
    if shutil.which('xhs') is None: return 127, '', 'xhs-cli 未安装，请在后端环境安装 xhs-cli'
    p = await asyncio.create_subprocess_exec('xhs', *args, env={**os.environ, 'XHS_HOME': str(XHS_HOME)}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate(); return p.returncode or 0, out.decode(errors='replace'), err.decode(errors='replace')
@router.get('/status')
async def status(_: CurrentAdmin):
    code, out, err = await _run('whoami', '--json')
    if code: return {'connected': False, 'installed': code != 127, 'message': err.strip()}
    try: profile = json.loads(out)
    except json.JSONDecodeError: profile = {'raw': out.strip()}
    return {'connected': True, 'installed': True, 'profile': profile}
@router.post('/login')
async def login(payload: LoginPayload, _: CurrentAdmin):
    XHS_HOME.mkdir(parents=True, exist_ok=True); code, out, err = await _run('login', '--cookie', payload.cookie)
    if code: raise HTTPException(400, detail=err.strip() or out.strip() or '小红书登录失败')
    return {'message': '小红书登录态已保存'}
@router.post('/posts')
async def post(payload: PostPayload, _: CurrentAdmin):
    args = ['post', payload.title, '--content', payload.content]
    for image in payload.images:
        path = Path(image).expanduser().resolve()
        if not path.is_file(): raise HTTPException(400, detail=f'图片不存在：{image}')
        args.extend(['--image', str(path)])
    code, out, err = await _run(*args, '--json')
    if code: raise HTTPException(502, detail=err.strip() or out.strip() or '发布失败')
    try: result = json.loads(out)
    except json.JSONDecodeError: result = {'raw': out.strip()}
    return {'message': '笔记发布成功', 'result': result}
