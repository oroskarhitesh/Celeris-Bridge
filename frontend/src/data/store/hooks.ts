/**
 * The typed hooks. Components use these, never the untyped originals.
 */

import { useDispatch, useSelector } from "react-redux";

import type { AppDispatch, RootState } from "./store";

export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();
