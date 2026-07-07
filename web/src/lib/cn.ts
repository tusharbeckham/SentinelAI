import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * The class merge helper every copy-paste registry (shadcn, Magic UI, Skiper UI,
 * Vengeance UI, Bklit) expects to exist at this exact path. Vendoring it means
 * their component source drops in unmodified.
 */
export function cn(...inputs: ClassValue[]): string {
	return twMerge(clsx(inputs))
}
