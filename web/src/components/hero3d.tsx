/**
 * NetworkHero -- the scroll-driven hero for the SentinelAI console.
 *
 * THE SUBJECT IS THE MODEL, NOT A MACHINE.
 *
 * Earlier versions rendered an imaginary optical instrument. It was pretty and
 * it was meaningless: nothing on screen came from the pipeline, so it could
 * have fronted any product at all. This version renders the one thing that is
 * actually ours -- the scored test set, drawn in the space the model sees.
 *
 * The geometry is literal:
 *   x, z  PCA of the 40 standardised features (PC1 22.5%, PC2 12.5% of variance)
 *   y     the model log-odds for that window
 *
 * Because the vertical axis IS the score, the operating threshold is not a
 * metaphor -- it is an exact horizontal plane at logit(0.6303). Everything
 * above it fires, everything below waits. Colour is ground truth. So the
 * central problem of the whole project becomes visible as geometry: the ten
 * benign points above the plane are the false positives an analyst pays for,
 * and the twenty coloured points stranded below it are the attacks we miss.
 *
 * The projection is recomputed from artifacts/scored_test_windows.csv into
 * public/data/embedding.json, and it reconciles with the reported operating
 * point exactly: 49 alerts, precision 0.7959, recall 0.6610.
 */
import { useEffect, useRef, useState, type RefObject } from "react"
import * as THREE from "three"
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js"
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js"
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js"
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js"
import { motion } from "motion/react"
import { useReducedMotion } from "@/components/anime"
import { cn } from "@/lib/cn"
import { HeroHud } from "@/components/herohud"
import type { Bundle, LiveStatus } from "@/lib/data"

const CANVAS = 0x0a0b0d
const WATCH = 0xde9255
const ALARM = 0xe97366

/*
 * Ground-truth palette, ordered to match families[] in embedding.json.
 * Benign is deliberately desaturated slate rather than a colour: 11,257 of the
 * 11,326 points are benign, so anything saturated would drown the six attack
 * families that are the only reason to look at this at all.
 */
const FAMILY_COLOR: number[] = [
	0x39414f, // benign
	0xe97366, // brute_force
	0xbf8eda, // dns_tunnel
	0xde9255, // dos
	0xe0b15a, // exfil
	0x5e9fe8, // lateral_movement
	0x72bc8f, // portscan
]

/** Deterministic PRNG (seed 7, like the pipeline) so the layout never changes. */
function lcg(seed: number) {
	let s = seed >>> 0
	return () => {
