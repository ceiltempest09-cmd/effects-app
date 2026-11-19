#!/usr/bin/env python3
"""Export EdgeTAM (phase 1) to ncnn: image encoder + prompt-encoder/mask-decoder.

Usage: python export_ncnn.py --repo EdgeTAM --out out [--cmake Effects/shared/cpp/CMakeLists.txt]
Writes out/edgetam_encoder.{param,bin}, out/edgetam_decoder.{param,bin}, out/report.txt
"""
import argparse, collections, hashlib, os, re, sys

import numpy as np
import torch

ap = argparse.ArgumentParser()
ap.add_argument("--repo", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--cmake", default="")
ap.add_argument("--points", type=int, default=8)
args = ap.parse_args()

repo = os.path.abspath(args.repo)
out = os.path.abspath(args.out)
os.makedirs(out, exist_ok=True)
sys.path.insert(0, repo)
os.chdir(repo)

# The backbone asks timm for pretrained ImageNet weights; the checkpoint overwrites them anyway.
import sam2.modeling.backbones.timm as sam_timm

_create = sam_timm.create_model
sam_timm.create_model = lambda *a, **k: _create(*a, **{**k, "pretrained": False})

from sam2.build_sam import build_sam2

N = args.points
model = build_sam2("configs/edgetam.yaml", os.path.join(repo, "checkpoints", "edgetam.pt"), device="cpu", apply_postprocessing=False)
model.eval()

try:
    from timm.utils import reparameterize_model
    model.image_encoder.trunk.body = reparameterize_model(model.image_encoder.trunk.body)
    print("RepViT re-parameterized")
except Exception as e:  # not fatal: batchnorm just stays in the graph
    print("reparameterize skipped:", e)


class Encoder(torch.nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, image):
        fpn = self.m.forward_image(image)["backbone_fpn"]
        feat = fpn[2]
        if self.m.directly_add_no_mem_embed:
            feat = feat + self.m.no_mem_embed.reshape(1, -1, 1, 1)
        return feat, fpn[0], fpn[1]


class Decoder(torch.nn.Module):
    """Points in 1024 space (boxes = 2 points, labels 2 and 3). Label -1 = unused slot.

    Point embedding is rewritten with plain arithmetic (no boolean-mask indexing) so it traces."""

    def __init__(self, m):
        super().__init__()
        self.m = m
        pe = m.sam_prompt_encoder
        self.register_buffer("dense_pe", pe.get_dense_pe().detach().clone())
        self.register_buffer("gauss", pe.pe_layer.positional_encoding_gaussian_matrix.detach().clone())
        w = torch.stack([pe.point_embeddings[i].weight.detach()[0] for i in range(4)])  # 4,256
        self.register_buffer("kind", w)
        self.register_buffer("not_pt", pe.not_a_point_embed.weight.detach().clone().reshape(1, 1, -1))
        self.register_buffer("no_mask", pe.no_mask_embed.weight.detach().clone().reshape(1, -1, 1, 1))

    def forward(self, image_embed, high0, high1, point_coords, point_labels):
        # same as PromptEncoder._embed_points with pad=True
        pts = point_coords + 0.5
        pts = torch.cat([pts, torch.zeros(1, 1, 2)], dim=1)
        lab = torch.cat([point_labels, -torch.ones(1, 1)], dim=1).unsqueeze(-1)  # 1,N+1,1
        c = 2 * (pts / 1024.0) - 1
        c = 2 * 3.141592653589793 * (c @ self.gauss)
        emb = torch.cat([torch.sin(c), torch.cos(c)], dim=-1)  # 1,N+1,256
        valid = (lab > -0.5).float()
        emb = emb * valid + (1.0 - valid) * self.not_pt
        for k in range(4):
            emb = emb + (lab - float(k)).abs().lt(0.5).float() * self.kind[k].reshape(1, 1, -1)
        res = self.m.sam_mask_decoder(
            image_embeddings=image_embed, image_pe=self.dense_pe, sparse_prompt_embeddings=emb,
            dense_prompt_embeddings=self.no_mask, multimask_output=True, repeat_image=False,
            high_res_features=[high0, high1])
        return res[0], res[1]  # masks (1,3,256,256), iou (1,3)


enc, dec = Encoder(model).eval(), Decoder(model).eval()
img = torch.randn(1, 3, 1024, 1024)
with torch.no_grad():
    e0, e1, e2 = enc(img)
    print("encoder out", tuple(e0.shape), tuple(e1.shape), tuple(e2.shape))
    pc = torch.zeros(1, N, 2)
    pc[0, 0] = torch.tensor([512.0, 512.0])
    pl = -torch.ones(1, N)
    pl[0, 0] = 1
    ref_masks, ref_iou = dec(e0, e1, e2, pc, pl)
    print("decoder out", tuple(ref_masks.shape), tuple(ref_iou.shape))
    sp, dn = model.sam_prompt_encoder(points=(pc, pl), boxes=None, masks=None)
    orig = model.sam_mask_decoder(image_embeddings=e0, image_pe=model.sam_prompt_encoder.get_dense_pe(),
        sparse_prompt_embeddings=sp, dense_prompt_embeddings=dn, multimask_output=True, repeat_image=False,
        high_res_features=[e1, e2])
    print("rewrite vs original decoder max|diff|:", (orig[0] - ref_masks).abs().max().item())

import pnnx


def export(mod, inputs, name):
    pt = os.path.join(out, name + ".pt")
    try:
        pnnx.export(mod, pt, inputs, fp16=True)
    except TypeError:
        pnnx.export(mod, pt, inputs)
    for ext in ("param", "bin"):
        src = os.path.join(out, name + ".ncnn." + ext)
        dst = os.path.join(out, name + "." + ext)
        os.replace(src, dst)


export(enc, (img,), "edgetam_encoder")
export(dec, (e0.detach(), e1.detach(), e2.detach(), pc, pl), "edgetam_decoder")

lines = []
wanted = set()
if args.cmake and os.path.exists(args.cmake):
    txt = open(args.cmake).read()
    m = re.search(r"set\(NCNN_LAYERS(.*?)\)", txt, re.S)
    wanted = set(m.group(1).split()) if m else set(txt.split())

for name in ("edgetam_encoder", "edgetam_decoder"):
    pth = os.path.join(out, name + ".param")
    ops = collections.Counter()
    for ln in open(pth).read().splitlines()[2:]:
        parts = ln.split()
        if parts:
            ops[parts[0]] += 1
    lines.append("== %s operators (%d layers)" % (name, sum(ops.values())))
    for k, v in sorted(ops.items()):
        flag = ""
        if wanted and k.lower() not in wanted:
            flag = "   <-- NOT in NCNN_LAYERS"
        lines.append("  %-24s %4d%s" % (k, v, flag))
    b = os.path.join(out, name + ".bin")
    sha = hashlib.sha256(open(b, "rb").read()).hexdigest()
    shp = hashlib.sha256(open(pth, "rb").read()).hexdigest()
    lines.append("  %s.param %d bytes sha256 %s" % (name, os.path.getsize(pth), shp))
    lines.append("  %s.bin   %d bytes sha256 %s" % (name, os.path.getsize(b), sha))

# Best-effort numeric check against ncnn (CPU). Failure here does not fail the job.
try:
    import ncnn

    def run(name, feeds):
        net = ncnn.Net()
        net.opt.use_vulkan_compute = False
        net.load_param(os.path.join(out, name + ".param"))
        net.load_model(os.path.join(out, name + ".bin"))
        ex = net.create_extractor()
        for k, v in feeds.items():
            ex.input(k, ncnn.Mat(np.ascontiguousarray(v.squeeze(0).numpy() if v.dim() == 4 else v.numpy().reshape(v.shape[-2:]) if v.dim() == 3 else v.numpy().reshape(-1))).clone())
        res = []
        for o in net.output_names():
            r, m = ex.extract(o)
            res.append(np.array(m).reshape(-1))
        return net.input_names(), net.output_names(), res

    ins, outs, res = run("edgetam_encoder", {"in0": img})
    lines.append("encoder ncnn inputs %s outputs %s" % (ins, outs))
    for r, t in zip(res, (e0, e1, e2)):
        lines.append("  encoder max|diff| %.5f (ref max %.3f)" % (np.abs(r - t.detach().numpy().reshape(-1)).max(), np.abs(t.detach().numpy()).max()))
    ins, outs, res = run("edgetam_decoder", {"in0": e0.detach(), "in1": e1.detach(), "in2": e2.detach(), "in3": pc, "in4": pl})
    lines.append("decoder ncnn inputs %s outputs %s" % (ins, outs))
    for r, t in zip(res, (ref_masks, ref_iou)):
        lines.append("  decoder max|diff| %.5f (ref max %.3f)" % (np.abs(r - t.detach().numpy().reshape(-1)).max(), np.abs(t.detach().numpy()).max()))
except Exception as e:
    lines.append("ncnn check skipped/failed: %r" % (e,))

report = "\n".join(lines)
print(report)
open(os.path.join(out, "report.txt"), "w").write(report + "\n")
