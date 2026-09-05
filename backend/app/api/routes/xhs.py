from __future__ import annotations
import asyncio, json, os, shutil
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from app.api.deps import CurrentAdmin
router = APIRouter(prefix='/xhs', tags=['Xiaohongshu'])
XHS_HOME = Path(os.getenv('XHS_CLI_HOME', '/tmp/xsentinel-xhs'))
UPLOAD_DIR = Path(os.getenv('XHS_UPLOAD_DIR', '/var/lib/xsentinel/xhs-uploads'))
class LoginPayload(BaseModel):
    a1: str = Field(min_length=3); web_session: str = Field(min_length=3)
class PostPayload(BaseModel):
    title: str = Field(min_length=1, max_length=80); content: str = Field(min_length=1, max_length=20000); images: list[str] = Field(min_length=1, max_length=18)
async def _run(*args: str):
    if shutil.which('xhs') is None: return 127, '', 'xhs-cli 未安装，请在后端环境安装 xhs-cli'
    p = await asyncio.create_subprocess_exec('xhs', *args, env={**os.environ, 'XHS_HOME': str(XHS_HOME)}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE); out, err = await p.communicate(); return p.returncode or 0, out.decode(errors='replace'), err.decode(errors='replace')
@router.get('/status')
async def status(_: CurrentAdmin):
    code, out, err = await _run('whoami', '--json')
    if code: return {'connected': False, 'installed': code != 127, 'message': err.strip()}
    try: profile = json.loads(out)
    except json.JSONDecodeError: profile = {'raw': out.strip()}
    return {'connected': True, 'installed': True, 'profile': profile}
@router.post('/login')
async def login(payload: LoginPayload, _: CurrentAdmin):
    XHS_HOME.mkdir(parents=True, exist_ok=True); code, out, err = await _run('login', '--cookie', f'a1={payload.a1}; web_session={payload.web_session}')
    if code: raise HTTPException(400, detail=err.strip() or out.strip() or '小红书登录失败')
    return {'message': '小红书登录态已保存'}
@router.post('/uploads')
async def upload(files: list[UploadFile] = File(...), _: CurrentAdmin = None):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True); result = []
    for file in files:
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}: raise HTTPException(400, detail='仅支持 JPG、PNG、WebP 图片')
        target = UPLOAD_DIR / f'{os.urandom(12).hex()}{suffix}'; target.write_bytes(await file.read()); target.chmod(0o640); result.append({'path': str(target)})
    return {'files': result}
@router.post('/posts')
async def post(payload: PostPayload, _: CurrentAdmin):
    args = ['post', payload.title, '--content', payload.content]
    for image in payload.images:
        path = Path(image).resolve()
        if not path.is_file() or UPLOAD_DIR not in path.parents: raise HTTPException(400, detail='图片路径无效')
        args.extend(['--image', str(path)])
    code, out, err = await _run(*args, '--json')
    if code: raise HTTPException(502, detail=err.strip() or out.strip() or '发布失败')
    try: result = json.loads(out)
    except json.JSONDecodeError: result = {'raw': out.strip()}
    return {'message': '笔记发布成功', 'result': result}
