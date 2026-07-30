/**
 * HeroHud -- the instrument chrome layered over the embedding scene.
 *
 * WHY THIS IS NOT AN SVG WITH A VIEWBOX ANY MORE.
 *
 * Version 1 drew everything into a fixed 1600x900 viewBox with
 * preserveAspectRatio="xMidYMid slice". Slice scales to cover, so on any
 * viewport narrower or shorter than 16:9 the box is cropped inward and the
 * corner readouts march toward the middle of the screen -- straight through
 * the subtitle. It looked fine at one aspect ratio and broken at every other.
 *
 * So the chrome is now laid out with normal flow and absolute corners. It
 * cannot collide with the title, because it is anchored to the edges and the
 * title owns the middle. The only SVG left is the axis triad, which lives in
 * its own small box where a viewBox is harmless.
 *
 * Nothing here is invented: every readout is a real figure from the run.
 */
import { motion } from "motion/react"
import { cn } from "@/lib/cn"

type Props = { act: number; className?: string }

/** Ground-truth families, in the colour order the scene uses. */
const LEGEND: Array<{ k: string; c: string }> = [
	{ k: "benign", c: "#39414f" },
	{ k: "brute_force", c: "#e97366" },
